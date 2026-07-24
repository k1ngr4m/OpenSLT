from __future__ import annotations

import typing
import hashlib
import posixpath
import re
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import uuid4
from xml.dom import Node, minidom
from xml.parsers.expat import ExpatError

import asyncssh

from app.core.config import settings
from app.core.security import decrypt_secret
from app.models import Resource


MAX_XML_BYTES = 1024 * 1024
TRASH_DIRECTORY = ".openslt-config-trash"
ORDER_TOOLS = {
    "ees_ef_vi_trader_binary_api_test": "ees_ef_vi_trader_api_test_conf",
    "ees_zf_trader_binary_api_test": "ees_zf_trader_api_test_conf",
}
FORBIDDEN_XML = re.compile(r"<!\s*(DOCTYPE|ENTITY)\b", re.IGNORECASE)
SYMBOL_CSV_ELEMENTS = {
    "futures": "fut_symbol_csv",
    "options": "opt_symbol_csv",
}


class OrderConfigError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass
class OrderConfigContext:
    resource_id: int
    tool: str
    prefix: str
    directory: str
    host: str
    port: int
    username: str
    password: typing.Union[str, None]
    private_key: typing.Union[str, None]


@dataclass
class SimulatedFile:
    content: str
    modified_at: datetime


def checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _tool_from_resource(resource: Resource) -> typing.Union[str, None]:
    configured = (resource.capabilities or {}).get("order_tool")
    if configured in ORDER_TOOLS:
        return configured
    directory_name = posixpath.basename(resource.remote_path.rstrip("/"))
    return directory_name if directory_name in ORDER_TOOLS else None


def resource_context(resource: Resource) -> OrderConfigContext:
    if resource.is_deleted:
        raise OrderConfigError("ORDER_RESOURCE_NOT_FOUND", "发单工具资源不存在", 404)
    if resource.resource_type != "order":
        raise OrderConfigError("ORDER_RESOURCE_REQUIRED", "该资源不是发单工具", 400)
    if not resource.is_enabled:
        raise OrderConfigError("ORDER_RESOURCE_DISABLED", "发单工具资源已停用", 409)
    tool = _tool_from_resource(resource)
    if not tool:
        raise OrderConfigError("ORDER_TOOL_UNKNOWN", "无法识别发单工具类型", 400)
    directory = resource.remote_path.strip()
    if not directory:
        raise OrderConfigError("ORDER_CONFIG_PATH_REQUIRED", "发单工具远端路径不能为空", 400)
    return OrderConfigContext(
        resource_id=resource.id,
        tool=tool,
        prefix=ORDER_TOOLS[tool],
        directory=directory,
        host=resource.host,
        port=resource.ssh_port,
        username=resource.username,
        password=decrypt_secret(resource.encrypted_password),
        private_key=decrypt_secret(resource.encrypted_private_key),
    )


def validate_filename(context: OrderConfigContext, filename: str) -> str:
    if not filename or len(filename) > 255 or filename != posixpath.basename(filename):
        raise OrderConfigError("INVALID_ORDER_CONFIG_NAME", "配置文件名不合法")
    pattern = rf"^{re.escape(context.prefix)}[A-Za-z0-9._-]*\.xml$"
    if not re.fullmatch(pattern, filename):
        raise OrderConfigError(
            "INVALID_ORDER_CONFIG_NAME",
            f"配置文件名必须以 {context.prefix} 开头并以 .xml 结尾",
        )
    return filename


def _node_to_dict(node: Node) -> dict:
    if node.nodeType == Node.ELEMENT_NODE:
        attributes = []
        if node.attributes:
            attributes = [
                {"name": node.attributes.item(index).name, "value": node.attributes.item(index).value}
                for index in range(node.attributes.length)
            ]
        return {
            "type": "element",
            "name": node.nodeName,
            "attributes": attributes,
            "text": None,
            "children": [_node_to_dict(child) for child in node.childNodes],
        }
    if node.nodeType == Node.COMMENT_NODE:
        node_type = "comment"
    elif node.nodeType == Node.CDATA_SECTION_NODE:
        node_type = "cdata"
    elif node.nodeType == Node.PROCESSING_INSTRUCTION_NODE:
        node_type = "processing_instruction"
    else:
        node_type = "text"
    return {
        "type": node_type,
        "name": node.nodeName if node_type == "processing_instruction" else None,
        "attributes": [],
        "text": node.nodeValue or "",
        "children": [],
    }


def parse_xml(content: str) -> typing.Tuple[str, dict]:
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise OrderConfigError("ORDER_CONFIG_ENCODING", "XML 必须使用 UTF-8 编码") from exc
    if len(encoded) > MAX_XML_BYTES:
        raise OrderConfigError("ORDER_CONFIG_TOO_LARGE", "XML 文件不能超过 1 MiB", 413)
    if FORBIDDEN_XML.search(content):
        raise OrderConfigError("UNSAFE_ORDER_CONFIG_XML", "XML 不允许包含 DOCTYPE 或实体声明")
    declaration_match = re.match(r"^\ufeff?\s*(<\?xml[^?]*\?>)", content, re.IGNORECASE)
    if declaration_match:
        encoding_match = re.search(r"\bencoding\s*=\s*['\"]([^'\"]+)['\"]", declaration_match.group(1), re.IGNORECASE)
        if encoding_match and encoding_match.group(1).lower().replace("-", "") != "utf8":
            raise OrderConfigError("ORDER_CONFIG_ENCODING", "XML 声明必须使用 UTF-8 编码")
    try:
        document = minidom.parseString(encoded)
    except (ExpatError, ValueError) as exc:
        raise OrderConfigError("INVALID_ORDER_CONFIG_XML", f"XML 格式错误：{exc}") from exc
    if not document.documentElement:
        raise OrderConfigError("INVALID_ORDER_CONFIG_XML", "XML 缺少根节点")
    declaration = declaration_match.group(1) if declaration_match else '<?xml version="1.0" encoding="utf-8"?>'
    return declaration, _node_to_dict(document.documentElement)


def update_symbol_csv_values(content: str, filenames: typing.Dict[str, str]) -> str:
    parse_xml(content)
    unsupported = set(filenames) - set(SYMBOL_CSV_ELEMENTS)
    if unsupported:
        raise OrderConfigError("ORDER_CONFIG_SYMBOL_CSV_INVALID", "存在不受支持的合约 CSV 类型")

    document = minidom.parseString(content.encode("utf-8"))
    type_by_element = {name.casefold(): contract_type for contract_type, name in SYMBOL_CSV_ELEMENTS.items()}
    elements: typing.Dict[str, typing.List[typing.Any]] = {contract_type: [] for contract_type in SYMBOL_CSV_ELEMENTS}
    for element in document.getElementsByTagName("*"):
        contract_type = type_by_element.get(element.tagName.casefold())
        if contract_type:
            elements[contract_type].append(element)

    for contract_type, matches in elements.items():
        element_name = SYMBOL_CSV_ELEMENTS[contract_type]
        if len(matches) > 1:
            raise OrderConfigError(
                "ORDER_CONFIG_SYMBOL_CSV_INVALID",
                f"XML 中 {element_name} 必须唯一",
                409,
            )
        if matches and contract_type not in filenames:
            raise OrderConfigError(
                "ORDER_CONFIG_SYMBOL_CSV_VALUE_REQUIRED",
                f"XML 包含 {element_name}，请先选择对应的合约 CSV",
                409,
            )
        if not matches and contract_type in filenames:
            raise OrderConfigError(
                "ORDER_CONFIG_SYMBOL_CSV_MISSING",
                f"XML 缺少 {element_name} 配置项",
                409,
            )

    changed = False
    for contract_type, filename in filenames.items():
        element = elements[contract_type][0]
        if element.getAttribute("value") != filename:
            element.setAttribute("value", filename)
            changed = True
    if not changed:
        return content

    updated = document.toxml(encoding="utf-8").decode("utf-8")
    parse_xml(updated)
    return updated


def config_detail(context: OrderConfigContext, filename: str, content: str, modified_at: datetime) -> dict:
    declaration, document = parse_xml(content)
    return {
        "name": filename,
        "size": len(content.encode("utf-8")),
        "modified_at": modified_at,
        "checksum": checksum(content),
        "content": content,
        "declaration": declaration,
        "document": document,
        "tool": context.tool,
        "simulated": settings.execution_mode == "simulated",
    }


def _sample_xml(tool: str) -> str:
    label = "EF" if tool.startswith("ees_ef") else "ZF"
    return f'''<?xml version="1.0" encoding="utf-8"?>
<tcp>
  <group_zf_tpi id="tpi" disp="{label} TPI">
    <tpi_instance_count disp="TPI_COUNT" default_value="" value="10" />
    <enter_times disp="TIMES" default_value="" value="40" />
  </group_zf_tpi>
  <tpl_tpi_0 id="tpl_tpi" disp="tpl_tpi">
    <group_broker id="rem_conf" disp="REM">
      <app_id disp="APP_ID" default_value="" value="1234" />
      <login_id disp="LOGIN_ID" default_value="" value="test001" />
      <password disp="PASSWORD" default_value="" value="1" />
      <rem_trade_ip disp="TRADE_IP" default_value="" value="180.1.1.130" />
    </group_broker>
    <group_new_order id="new_order" disp="NEW_ORDER">
      <exchange disp="EXCHANGE" default_value="" value="102" />
      <account disp="ACCOUNT" default_value="" value="100001" />
      <symbol disp="SYMBOL" default_value="" value="ag2210" />
      <price disp="PRICE" default_value="" value="1495.0000" />
    </group_new_order>
    <!-- additional scenario groups can be copied here -->
  </tpl_tpi_0>
</tcp>
'''


class SimulatedOrderConfigStore:
    def __init__(self) -> None:
        self._files: typing.Dict[int, typing.Dict[str, SimulatedFile]] = {}

    def files(self, context: OrderConfigContext) -> typing.Dict[str, SimulatedFile]:
        if context.resource_id not in self._files:
            filename = f"{context.prefix}.xml"
            self._files[context.resource_id] = {
                filename: SimulatedFile(_sample_xml(context.tool), datetime.now(timezone.utc))
            }
        return self._files[context.resource_id]

    def clear(self) -> None:
        self._files.clear()


simulated_store = SimulatedOrderConfigStore()


@asynccontextmanager
async def _sftp_client(context: OrderConfigContext) -> typing.AsyncIterator[asyncssh.SFTPClient]:
    options: typing.Dict[str, object] = {
        "host": context.host,
        "port": context.port,
        "username": context.username,
        "known_hosts": None,
        "connect_timeout": 15,
    }
    if context.password:
        options["password"] = context.password
    if context.private_key:
        options["client_keys"] = [asyncssh.import_private_key(context.private_key)]
    connection = await asyncssh.connect(**options)
    sftp = None
    try:
        sftp = await connection.start_sftp_client()
        yield sftp
    finally:
        if sftp is not None:
            sftp.exit()
            with suppress(Exception):
                await sftp.wait_closed()
        connection.close()
        with suppress(Exception):
            await connection.wait_closed()


def _path(context: OrderConfigContext, filename: str) -> str:
    return posixpath.join(context.directory.rstrip("/"), filename)


async def _read_remote_file(
    sftp: asyncssh.SFTPClient,
    context: OrderConfigContext,
    filename: str,
) -> typing.Tuple[str, asyncssh.SFTPAttrs]:
    path = _path(context, filename)
    try:
        attrs = await sftp.lstat(path)
    except asyncssh.SFTPNoSuchFile as exc:
        raise OrderConfigError("ORDER_CONFIG_NOT_FOUND", "配置文件不存在", 404) from exc
    if attrs.type != asyncssh.FILEXFER_TYPE_REGULAR:
        raise OrderConfigError("ORDER_CONFIG_NOT_REGULAR", "配置文件必须是普通文件")
    if attrs.size is not None and attrs.size > MAX_XML_BYTES:
        raise OrderConfigError("ORDER_CONFIG_TOO_LARGE", "XML 文件不能超过 1 MiB", 413)
    try:
        async with sftp.open(path, "r", encoding="utf-8", errors="strict") as remote_file:
            content = await remote_file.read(MAX_XML_BYTES + 1)
    except UnicodeDecodeError as exc:
        raise OrderConfigError("ORDER_CONFIG_ENCODING", "XML 必须使用 UTF-8 编码") from exc
    if len(content.encode("utf-8")) > MAX_XML_BYTES:
        raise OrderConfigError("ORDER_CONFIG_TOO_LARGE", "XML 文件不能超过 1 MiB", 413)
    return content, attrs


async def _write_remote_file(
    sftp: asyncssh.SFTPClient,
    context: OrderConfigContext,
    filename: str,
    content: str,
    permissions: typing.Union[int, None],
    replace: bool,
) -> None:
    target = _path(context, filename)
    temporary = _path(context, f".openslt-{uuid4().hex}.tmp")
    try:
        async with sftp.open(temporary, "w", encoding="utf-8", errors="strict") as remote_file:
            await remote_file.write(content)
        if permissions is not None:
            await sftp.setstat(temporary, asyncssh.SFTPAttrs(permissions=permissions))
        if replace:
            await sftp.posix_rename(temporary, target)
        else:
            await sftp.rename(temporary, target)
    finally:
        with suppress(asyncssh.SFTPError):
            await sftp.remove(temporary)


def _modified_at(value: typing.Union[int, None]) -> datetime:
    return datetime.fromtimestamp(value or 0, timezone.utc)


class OrderConfigService:
    async def list(self, resource: Resource) -> dict:
        context = resource_context(resource)
        if settings.execution_mode == "simulated":
            rows = [
                {
                    "name": name,
                    "size": len(item.content.encode("utf-8")),
                    "modified_at": item.modified_at,
                }
                for name, item in simulated_store.files(context).items()
            ]
        else:
            rows = []
            try:
                async with _sftp_client(context) as sftp:
                    async for entry in sftp.scandir(context.directory):
                        try:
                            validate_filename(context, entry.filename)
                        except OrderConfigError:
                            continue
                        if entry.attrs.type != asyncssh.FILEXFER_TYPE_REGULAR:
                            continue
                        rows.append(
                            {
                                "name": entry.filename,
                                "size": entry.attrs.size or 0,
                                "modified_at": _modified_at(entry.attrs.mtime),
                            }
                        )
            except OrderConfigError:
                raise
            except (asyncssh.Error, OSError) as exc:
                raise OrderConfigError("ORDER_CONFIG_SFTP_FAILED", f"读取远端配置目录失败：{exc}", 502) from exc
        rows.sort(key=lambda item: item["name"])
        return {
            "tool": context.tool,
            "directory": context.directory,
            "simulated": settings.execution_mode == "simulated",
            "files": rows,
        }

    async def read(self, resource: Resource, filename: str) -> dict:
        context = resource_context(resource)
        filename = validate_filename(context, filename)
        if settings.execution_mode == "simulated":
            item = simulated_store.files(context).get(filename)
            if not item:
                raise OrderConfigError("ORDER_CONFIG_NOT_FOUND", "配置文件不存在", 404)
            return config_detail(context, filename, item.content, item.modified_at)
        try:
            async with _sftp_client(context) as sftp:
                content, attrs = await _read_remote_file(sftp, context, filename)
            return config_detail(context, filename, content, _modified_at(attrs.mtime))
        except OrderConfigError:
            raise
        except (asyncssh.Error, OSError) as exc:
            raise OrderConfigError("ORDER_CONFIG_SFTP_FAILED", f"读取远端配置失败：{exc}", 502) from exc

    async def create(self, resource: Resource, name: str, source_name: str) -> dict:
        context = resource_context(resource)
        name = validate_filename(context, name)
        source_name = validate_filename(context, source_name)
        if name == source_name:
            raise OrderConfigError("ORDER_CONFIG_NAME_CONFLICT", "新配置文件名不能与模板相同", 409)
        if settings.execution_mode == "simulated":
            files = simulated_store.files(context)
            if name in files:
                raise OrderConfigError("ORDER_CONFIG_NAME_CONFLICT", "配置文件名已存在", 409)
            source = files.get(source_name)
            if not source:
                raise OrderConfigError("ORDER_CONFIG_NOT_FOUND", "模板配置不存在", 404)
            parse_xml(source.content)
            files[name] = SimulatedFile(source.content, datetime.now(timezone.utc))
            return config_detail(context, name, files[name].content, files[name].modified_at)
        try:
            async with _sftp_client(context) as sftp:
                if await sftp.exists(_path(context, name)):
                    raise OrderConfigError("ORDER_CONFIG_NAME_CONFLICT", "配置文件名已存在", 409)
                content, attrs = await _read_remote_file(sftp, context, source_name)
                parse_xml(content)
                await _write_remote_file(sftp, context, name, content, attrs.permissions, replace=False)
            return config_detail(context, name, content, datetime.now(timezone.utc))
        except OrderConfigError:
            raise
        except (asyncssh.Error, OSError) as exc:
            raise OrderConfigError("ORDER_CONFIG_SFTP_FAILED", f"创建远端配置失败：{exc}", 502) from exc

    async def update(self, resource: Resource, filename: str, content: str, expected_checksum: str) -> dict:
        context = resource_context(resource)
        filename = validate_filename(context, filename)
        parse_xml(content)
        if settings.execution_mode == "simulated":
            files = simulated_store.files(context)
            item = files.get(filename)
            if not item:
                raise OrderConfigError("ORDER_CONFIG_NOT_FOUND", "配置文件不存在", 404)
            if checksum(item.content) != expected_checksum:
                raise OrderConfigError("ORDER_CONFIG_CHANGED", "配置已被其他用户修改，请重新加载", 409)
            item.content = content
            item.modified_at = datetime.now(timezone.utc)
            return config_detail(context, filename, item.content, item.modified_at)
        try:
            async with _sftp_client(context) as sftp:
                current, attrs = await _read_remote_file(sftp, context, filename)
                if checksum(current) != expected_checksum:
                    raise OrderConfigError("ORDER_CONFIG_CHANGED", "配置已被其他用户修改，请重新加载", 409)
                await _write_remote_file(sftp, context, filename, content, attrs.permissions, replace=True)
            return config_detail(context, filename, content, datetime.now(timezone.utc))
        except OrderConfigError:
            raise
        except (asyncssh.Error, OSError) as exc:
            raise OrderConfigError("ORDER_CONFIG_SFTP_FAILED", f"保存远端配置失败：{exc}", 502) from exc

    async def rename(self, resource: Resource, filename: str, new_name: str, expected_checksum: str) -> dict:
        context = resource_context(resource)
        filename = validate_filename(context, filename)
        new_name = validate_filename(context, new_name)
        if filename == new_name:
            raise OrderConfigError("ORDER_CONFIG_NAME_CONFLICT", "新文件名与当前文件名相同", 409)
        if settings.execution_mode == "simulated":
            files = simulated_store.files(context)
            item = files.get(filename)
            if not item:
                raise OrderConfigError("ORDER_CONFIG_NOT_FOUND", "配置文件不存在", 404)
            if new_name in files:
                raise OrderConfigError("ORDER_CONFIG_NAME_CONFLICT", "配置文件名已存在", 409)
            if checksum(item.content) != expected_checksum:
                raise OrderConfigError("ORDER_CONFIG_CHANGED", "配置已被其他用户修改，请重新加载", 409)
            files[new_name] = files.pop(filename)
            files[new_name].modified_at = datetime.now(timezone.utc)
            return config_detail(context, new_name, files[new_name].content, files[new_name].modified_at)
        try:
            async with _sftp_client(context) as sftp:
                current, _ = await _read_remote_file(sftp, context, filename)
                parse_xml(current)
                if checksum(current) != expected_checksum:
                    raise OrderConfigError("ORDER_CONFIG_CHANGED", "配置已被其他用户修改，请重新加载", 409)
                if await sftp.exists(_path(context, new_name)):
                    raise OrderConfigError("ORDER_CONFIG_NAME_CONFLICT", "配置文件名已存在", 409)
                await sftp.rename(_path(context, filename), _path(context, new_name))
            return config_detail(context, new_name, current, datetime.now(timezone.utc))
        except OrderConfigError:
            raise
        except (asyncssh.Error, OSError) as exc:
            raise OrderConfigError("ORDER_CONFIG_SFTP_FAILED", f"重命名远端配置失败：{exc}", 502) from exc

    async def delete(self, resource: Resource, filename: str, expected_checksum: str) -> str:
        context = resource_context(resource)
        filename = validate_filename(context, filename)
        if settings.execution_mode == "simulated":
            files = simulated_store.files(context)
            item = files.get(filename)
            if not item:
                raise OrderConfigError("ORDER_CONFIG_NOT_FOUND", "配置文件不存在", 404)
            if checksum(item.content) != expected_checksum:
                raise OrderConfigError("ORDER_CONFIG_CHANGED", "配置已被其他用户修改，请重新加载", 409)
            del files[filename]
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            return f"{timestamp}-{uuid4().hex[:8]}-{filename}"
        try:
            async with _sftp_client(context) as sftp:
                current, _ = await _read_remote_file(sftp, context, filename)
                if checksum(current) != expected_checksum:
                    raise OrderConfigError("ORDER_CONFIG_CHANGED", "配置已被其他用户修改，请重新加载", 409)
                trash_directory = _path(context, TRASH_DIRECTORY)
                await sftp.makedirs(trash_directory, exist_ok=True)
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                trash_name = f"{timestamp}-{uuid4().hex[:8]}-{filename}"
                await sftp.rename(_path(context, filename), posixpath.join(trash_directory, trash_name))
                return trash_name
        except OrderConfigError:
            raise
        except (asyncssh.Error, OSError) as exc:
            raise OrderConfigError("ORDER_CONFIG_SFTP_FAILED", f"删除远端配置失败：{exc}", 502) from exc


order_config_service = OrderConfigService()

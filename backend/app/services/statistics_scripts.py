from __future__ import annotations

import hashlib
import posixpath
import re
import typing
from contextlib import suppress

import asyncssh

from app.core.time import from_unix_timestamp
from app.models import Resource
from app.services.workflow_capture import _ssh_options


MAX_STATISTICS_SCRIPT_BYTES = 1024 * 1024
SCRIPT_NAME = re.compile(r"^[A-Za-z0-9._-]+\.py$")


class StatisticsScriptError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def validate_resource(resource: Resource) -> str:
    if resource.is_deleted:
        raise StatisticsScriptError("STATISTICS_RESOURCE_NOT_FOUND", "解析工具资源不存在", 404)
    if resource.resource_type != "parser":
        raise StatisticsScriptError("PARSER_RESOURCE_REQUIRED", "该资源不是解析工具", 400)
    if not resource.is_enabled:
        raise StatisticsScriptError("STATISTICS_RESOURCE_DISABLED", "解析工具资源已停用", 409)
    directory = resource.remote_path.strip().rstrip("/")
    if not directory:
        raise StatisticsScriptError("STATISTICS_SCRIPT_PATH_REQUIRED", "解析工具远端路径不能为空", 400)
    return directory


def validate_filename(filename: str) -> str:
    if not SCRIPT_NAME.fullmatch(filename) or filename != posixpath.basename(filename):
        raise StatisticsScriptError("STATISTICS_SCRIPT_NAME_INVALID", "统计脚本文件名不合法", 400)
    return filename


async def _read_script(sftp: typing.Any, path: str, attrs: typing.Any) -> bytes:
    if attrs.type != asyncssh.FILEXFER_TYPE_REGULAR:
        raise StatisticsScriptError("STATISTICS_SCRIPT_INVALID", "统计脚本必须是普通文件", 409)
    if (attrs.size or 0) > MAX_STATISTICS_SCRIPT_BYTES:
        raise StatisticsScriptError("STATISTICS_SCRIPT_TOO_LARGE", "统计脚本不能超过 1 MiB", 413)
    async with sftp.open(path, "rb") as remote_file:
        content = await remote_file.read(MAX_STATISTICS_SCRIPT_BYTES + 1)
    if isinstance(content, str):
        content = content.encode("utf-8")
    if len(content) > MAX_STATISTICS_SCRIPT_BYTES:
        raise StatisticsScriptError("STATISTICS_SCRIPT_TOO_LARGE", "统计脚本不能超过 1 MiB", 413)
    return content


def _detail(filename: str, attrs: typing.Any, content: bytes) -> dict[str, typing.Any]:
    return {
        "name": filename,
        "size": len(content),
        "modified_at": from_unix_timestamp(attrs.mtime or 0),
        "checksum": hashlib.sha256(content).hexdigest(),
        "executable": bool((attrs.permissions or 0) & 0o111),
    }


class StatisticsScriptService:
    async def list(self, resource: Resource) -> dict[str, typing.Any]:
        directory = validate_resource(resource)
        connection = None
        sftp = None
        rows: list[dict[str, typing.Any]] = []
        try:
            connection = await asyncssh.connect(**_ssh_options(resource))
            sftp = await connection.start_sftp_client()
            async for entry in sftp.scandir(directory):
                if not SCRIPT_NAME.fullmatch(entry.filename):
                    continue
                if entry.attrs.type != asyncssh.FILEXFER_TYPE_REGULAR:
                    continue
                try:
                    content = await _read_script(
                        sftp, posixpath.join(directory, entry.filename), entry.attrs
                    )
                except StatisticsScriptError:
                    continue
                rows.append(_detail(entry.filename, entry.attrs, content))
        except StatisticsScriptError:
            raise
        except (asyncssh.Error, OSError) as exc:
            raise StatisticsScriptError(
                "STATISTICS_SCRIPT_SFTP_FAILED", f"读取远端统计脚本失败：{exc}", 502
            ) from exc
        finally:
            if sftp:
                with suppress(Exception):
                    sftp.exit()
            if connection:
                connection.close()
                with suppress(Exception):
                    await connection.wait_closed()
        rows.sort(key=lambda item: item["name"])
        return {"directory": directory, "files": rows}

    async def read(self, resource: Resource, filename: str) -> dict[str, typing.Any]:
        directory = validate_resource(resource)
        filename = validate_filename(filename)
        connection = None
        sftp = None
        try:
            connection = await asyncssh.connect(**_ssh_options(resource))
            sftp = await connection.start_sftp_client()
            path = posixpath.join(directory, filename)
            attrs = await sftp.lstat(path)
            content = await _read_script(sftp, path, attrs)
            return {**_detail(filename, attrs, content), "path": path}
        except StatisticsScriptError:
            raise
        except asyncssh.SFTPNoSuchFile as exc:
            raise StatisticsScriptError("STATISTICS_SCRIPT_NOT_FOUND", "统计脚本不存在", 404) from exc
        except (asyncssh.Error, OSError) as exc:
            raise StatisticsScriptError(
                "STATISTICS_SCRIPT_SFTP_FAILED", f"读取远端统计脚本失败：{exc}", 502
            ) from exc
        finally:
            if sftp:
                with suppress(Exception):
                    sftp.exit()
            if connection:
                connection.close()
                with suppress(Exception):
                    await connection.wait_closed()


statistics_script_service = StatisticsScriptService()

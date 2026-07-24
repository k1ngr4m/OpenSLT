from __future__ import annotations

import csv
import hashlib
import os
import posixpath
import re
import shlex
import struct
import tempfile
import typing
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import asyncssh
from pymysql.cursors import SSCursor
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.adapters.database import DatabaseOperationError, mysql_adapter, validate_database
from app.core.compat import to_thread
from app.core.config import settings
from app.core.security import decrypt_secret
from app.models import (
    Artifact,
    ConfigurationCaptureItem,
    ConfigurationCaptureSnapshot,
    ContractDataFile,
    Resource,
    RunStep,
    ScenarioWorkflowNode,
    ScenarioWorkflowVersion,
    TestRun,
    TestScenario,
)
from app.services.order_configs import OrderConfigError, order_config_service, update_symbol_csv_values

SLNIC_NODE_TYPES = {"slnic_start_capture", "slnic_stop_capture", "slnic_merge_capture"}
NODE_TYPES = {
    "server_config",
    "database_config",
    "wiring_confirmation",
    "order_preparation",
    *SLNIC_NODE_TYPES,
}
SERVER_FIELDS = {
    "rem": {"ip", "nic_model", "machine_model", "os_version", "cpu_model"},
    "market": {"ip", "os_version", "cpu_model"},
    "order": {"ip", "os_version", "cpu_model"},
}
FIELD_LABELS = {
    "ip": "IP 地址",
    "nic_model": "网卡型号",
    "machine_model": "机器型号",
    "os_version": "操作系统版本",
    "cpu_model": "CPU 型号",
}
SERVER_COMMANDS = {
    "ip": "ip -o -4 addr show scope global | awk '{print $2, $4}'",
    "nic_model": "lspci -Dnn | grep -Ei 'ethernet|network'",
    "machine_model": "cat /sys/class/dmi/id/product_name /sys/class/dmi/id/board_name 2>/dev/null | sed '/^$/d'",
    "os_version": "cat /etc/redhat-release 2>/dev/null || . /etc/os-release && printf '%s %s\\n' \"$NAME\" \"$VERSION\"",
    "cpu_model": "lscpu | grep -E '^(Model name|CPU\\(s\\)|CPU max MHz):'",
}
GLOBAL_SETTING_KEYS = [
    "CLIENT_REQ_BIND_CPU", "MARKET_RESP_BIND_CPU", "RINGBUFFER_RSP_BIND_CPU",
    "TCP_SERVER_BIND_CPU", "CLIENT_REQ_ENABLE", "CLIENT_REQ_USING_DEV",
    "MARKET_RESP_ENABLE", "MARKET_RESQ_DEV", "REM_TO_MKT_MESSAGE_DROPCOPY_ENABLE",
    "CLIENT_TO_REM_MESSAGE_DROPCOPY_ENABLE", "MARKET_SESSION_IDLE_REPROT_LOG",
    "ACCOUNT_QUANTITY", "WARM_ORDER_REPORT_USEC", "ENABLE_PERF_COUNTER",
    "ENABLE_RINGBUFFER_RSP", "ENABLE_RINGBUFFER_REQ", "ASYNC_MKT_MSG_PROC",
    "USER_TOKEN_CANCEL_ENABLE", "CLIENT_OT_CONNECT_MODE", "EXANIC_IP_FILTER_FLAG",
    "ENABLE_REPORT_TIMESTAMP", "X25_KEY_VALUE",
]
KEY_COLUMN_CANDIDATES = ["setting_name", "name", "setting_key", "key", "param_name"]
VALUE_COLUMN_CANDIDATES = ["setting_value", "value", "param_value"]
INTERFACE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
CONTRACT_TABLES = {"futures": "t_close_report", "options": "t_close_report_opt"}
CONTRACT_TYPE_LABELS = {"futures": "期货", "options": "期权"}


class WorkflowError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, *, errors: typing.Optional[list] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.errors = errors or []


def load_version(db: Session, version_id: int) -> ScenarioWorkflowVersion:
    version = db.scalar(
        select(ScenarioWorkflowVersion)
        .where(ScenarioWorkflowVersion.id == version_id)
        .options(selectinload(ScenarioWorkflowVersion.nodes))
    )
    if not version:
        raise WorkflowError("WORKFLOW_NOT_FOUND", "工作流不存在", 404)
    return version


def create_draft(db: Session, scenario: TestScenario, actor_id: int, resource_ids: list[int]) -> ScenarioWorkflowVersion:
    next_version = (db.scalar(select(func.max(ScenarioWorkflowVersion.version_no)).where(ScenarioWorkflowVersion.scenario_id == scenario.id)) or 0) + 1
    draft = ScenarioWorkflowVersion(
        scenario_id=scenario.id,
        version_no=next_version,
        status="draft",
        revision=1,
        resource_ids=list(resource_ids),
        created_by=actor_id,
    )
    db.add(draft)
    db.flush()
    scenario.draft_workflow_version_id = draft.id
    scenario.workflow_status = "draft"
    if not scenario.published_workflow_version_id:
        scenario.is_enabled = False
    return draft


def clone_published_to_draft(db: Session, scenario: TestScenario, actor_id: int) -> ScenarioWorkflowVersion:
    if scenario.draft_workflow_version_id:
        return load_version(db, scenario.draft_workflow_version_id)
    if not scenario.published_workflow_version_id:
        return create_draft(db, scenario, actor_id, scenario.default_resource_ids)
    published = load_version(db, scenario.published_workflow_version_id)
    draft = create_draft(db, scenario, actor_id, published.resource_ids)
    copy_version_contents(db, published, draft, actor_id)
    return draft


def copy_version_contents(
    db: Session,
    source: ScenarioWorkflowVersion,
    target: ScenarioWorkflowVersion,
    actor_id: int,
) -> None:
    _ = actor_id
    for source_node in source.nodes:
        target.nodes.append(ScenarioWorkflowNode(
            node_key=str(uuid4()), position=source_node.position, node_type=source_node.node_type,
            name=source_node.name, config=dict(source_node.config or {}),
        ))
    db.flush()


def resource_map(db: Session, version: ScenarioWorkflowVersion) -> dict[str, Resource]:
    resources = list(db.scalars(select(Resource).where(Resource.id.in_(version.resource_ids))).all()) if version.resource_ids else []
    return {item.resource_type: item for item in resources}


def validate_structure(db: Session, scenario: TestScenario, version: ScenarioWorkflowVersion) -> list[dict]:
    errors: list[dict] = []
    resources = resource_map(db, version)
    slnic_state = "idle"
    if not version.nodes:
        errors.append({"field": "nodes", "message": "主流程至少需要一个节点"})
    for node in version.nodes:
        config = node.config or {}
        prefix = {"node_key": node.node_key}
        if node.node_type not in NODE_TYPES:
            errors.append({**prefix, "field": "node_type", "message": "不支持的节点类型"})
            continue
        if not node.name.strip():
            errors.append({**prefix, "field": "name", "message": "节点名称不能为空"})
        if node.node_type == "server_config":
            targets = config.get("targets") or []
            if not targets:
                errors.append({**prefix, "field": "targets", "message": "至少选择一台服务器"})
            seen: set[str] = set()
            for target in targets:
                role = target.get("resource_type")
                fields = target.get("fields") or []
                if role in seen:
                    errors.append({**prefix, "field": "targets", "message": f"资源角色 {role} 不能重复"})
                seen.add(role)
                if role not in SERVER_FIELDS or role not in resources:
                    errors.append({**prefix, "field": "targets", "message": f"场景资源池缺少 {role or '未知'} 服务器"})
                if not fields or any(field not in SERVER_FIELDS.get(role, set()) for field in fields):
                    errors.append({**prefix, "field": "fields", "message": f"{role or '服务器'} 的采集项无效"})
        elif node.node_type == "database_config":
            resource = resources.get("database")
            database_name = str(config.get("database_name") or "")
            keys = config.get("keys") or []
            if not resource:
                errors.append({**prefix, "field": "resource", "message": "场景资源池缺少数据库资源"})
            elif database_name not in (resource.database_names or []):
                errors.append({**prefix, "field": "database_name", "message": "配置数据库不在资源白名单中"})
            if not keys or any(key not in GLOBAL_SETTING_KEYS for key in keys):
                errors.append({**prefix, "field": "keys", "message": "至少选择一个受支持的配置项"})
        elif node.node_type == "order_preparation":
            if "order" not in resources:
                errors.append({**prefix, "field": "resource", "message": "场景资源池缺少发单工具资源"})
            if not str(config.get("xml_filename") or "").strip():
                errors.append({**prefix, "field": "xml_filename", "message": "XML 配置为必填项"})
            interface = str(config.get("network_interface") or "")
            if interface and not INTERFACE_PATTERN.fullmatch(interface):
                errors.append({**prefix, "field": "network_interface", "message": "网卡接口名称不合法"})
            read_csv = config.get("read_symbol_csv")
            if read_csv not in (0, 1, False, True):
                errors.append({**prefix, "field": "read_symbol_csv", "message": "read_symbol_csv 只能为 0 或 1"})
            if bool(read_csv):
                if not config.get("trading_database_name"):
                    errors.append({**prefix, "field": "trading_database_name", "message": "请选择交易数据库"})
                if not config.get("contract_file_ids"):
                    errors.append({**prefix, "field": "contract_file_ids", "message": "至少选择一个合约 CSV"})
                preceding = [item for item in version.nodes if item.position < node.position and item.node_type == "database_config"]
                if not preceding:
                    errors.append({**prefix, "field": "database_node_key", "message": "发单节点前需要数据库配置节点"})
        elif node.node_type in SLNIC_NODE_TYPES:
            resource = resources.get("slnic")
            if not resource or resource.is_deleted or not resource.is_enabled:
                errors.append({**prefix, "field": "resource", "message": "场景资源池缺少已启用的 SLNIC 资源"})
            elif not resource.remote_path.strip():
                errors.append({**prefix, "field": "resource", "message": "SLNIC 资源未配置远端路径"})
            if node.node_type == "slnic_start_capture":
                if slnic_state == "capturing":
                    errors.append({**prefix, "field": "position", "message": "当前已有未停止的 SLNIC 抓包"})
                elif slnic_state == "stopped":
                    errors.append({**prefix, "field": "position", "message": "开始下一轮 SLNIC 抓包前需要先合并上一轮文件"})
                else:
                    slnic_state = "capturing"
            elif node.node_type == "slnic_stop_capture":
                if slnic_state != "capturing":
                    errors.append({**prefix, "field": "position", "message": "关闭 SLNIC 节点前需要先启动抓包"})
                else:
                    slnic_state = "stopped"
            elif slnic_state != "stopped":
                errors.append({**prefix, "field": "position", "message": "合并 pcapng 前需要先关闭 SLNIC 抓包"})
            else:
                slnic_state = "merged"
    return errors


def replace_draft(
    db: Session,
    scenario: TestScenario,
    version: ScenarioWorkflowVersion,
    *,
    expected_revision: int,
    resource_ids: list[int],
    nodes: list[dict],
) -> ScenarioWorkflowVersion:
    if version.status != "draft":
        raise WorkflowError("WORKFLOW_NOT_DRAFT", "只能修改草稿工作流", 409)
    if version.revision != expected_revision:
        raise WorkflowError("WORKFLOW_REVISION_CONFLICT", "工作流已被其他用户修改，请重新加载", 409)
    version.resource_ids = list(resource_ids)
    existing = {node.node_key: node for node in version.nodes}
    incoming_keys = {item["node_key"] for item in nodes}
    for node in existing.values():
        node.position += 10000
    db.flush()
    for key, node in list(existing.items()):
        if key not in incoming_keys:
            db.delete(node)
            existing.pop(key)
    db.flush()
    for position, item in enumerate(nodes, 1):
        node = existing.get(item["node_key"])
        if node:
            node.position = position
            node.node_type = item["node_type"]
            node.name = item["name"].strip()
            node.config = dict(item.get("config") or {})
        else:
            version.nodes.append(ScenarioWorkflowNode(
                node_key=item["node_key"],
                position=position,
                node_type=item["node_type"],
                name=item["name"].strip(),
                config=dict(item.get("config") or {}),
            ))
    version.revision += 1
    scenario.default_resource_ids = list(resource_ids)
    scenario.required_resource_types = sorted(resource_map(db, version))
    db.flush()
    return version


def workflow_payload(scenario: TestScenario, version: ScenarioWorkflowVersion, errors: list[dict]) -> dict:
    return {
        "scenario": scenario,
        "draft": {
            "id": version.id,
            "scenario_id": version.scenario_id,
            "version_no": version.version_no,
            "status": version.status,
            "revision": version.revision,
            "resource_ids": version.resource_ids,
            "published_by": version.published_by,
            "published_at": version.published_at,
            "created_at": version.created_at,
            "updated_at": version.updated_at,
            "nodes": [
                {"id": node.id, "node_key": node.node_key, "position": node.position, "node_type": node.node_type, "name": node.name, "config": node.config}
                for node in version.nodes
            ],
        },
        "published_version_id": scenario.published_workflow_version_id,
        "validation_errors": errors,
    }


def _ssh_options(resource: Resource) -> dict:
    options: dict[str, typing.Any] = {
        "host": resource.host, "port": resource.ssh_port, "username": resource.username,
        "known_hosts": None, "connect_timeout": 15,
    }
    password = decrypt_secret(resource.encrypted_password)
    private_key = decrypt_secret(resource.encrypted_private_key)
    if password:
        options["password"] = password
    if private_key:
        options["client_keys"] = [asyncssh.import_private_key(private_key)]
    return options


SIMULATED_VALUES = {
    "ip": "127.0.0.1/24",
    "nic_model": "Exablaze ExaNIC X25 *2",
    "machine_model": "ATZ-308 / ACE Z690 UNIFY (MS-7D28)",
    "os_version": "Red Hat Enterprise Linux Server release 7.9 (Maipo)",
    "cpu_model": "13th Gen Intel(R) Core(TM) i9-13900KS",
}


async def capture_server(
    db: Session,
    scenario: TestScenario,
    version: ScenarioWorkflowVersion,
    node: ScenarioWorkflowNode,
    *,
    scope: str,
    actor_id: typing.Optional[int],
    run_id: typing.Optional[int] = None,
    run_step_id: typing.Optional[int] = None,
    run_resources: typing.Optional[dict[str, Resource]] = None,
) -> list[ConfigurationCaptureSnapshot]:
    resources = run_resources or resource_map(db, version)
    snapshots = []
    for target in node.config.get("targets") or []:
        resource = resources.get(target.get("resource_type"))
        if not resource:
            raise WorkflowError("WORKFLOW_RESOURCE_MISSING", "运行资源与节点配置不匹配", 409)
        attempt = (db.scalar(
            select(func.count(ConfigurationCaptureSnapshot.id)).where(
                ConfigurationCaptureSnapshot.workflow_node_id == node.id,
                ConfigurationCaptureSnapshot.scope == scope,
                ConfigurationCaptureSnapshot.resource_id == resource.id,
                ConfigurationCaptureSnapshot.run_id == run_id if run_id is not None
                else ConfigurationCaptureSnapshot.run_id.is_(None),
            )
        ) or 0) + 1
        snapshot = ConfigurationCaptureSnapshot(
            scenario_id=scenario.id, workflow_version_id=version.id, workflow_node_id=node.id,
            run_id=run_id, run_step_id=run_step_id, scope=scope, source_type="server",
            resource_id=resource.id, status="running", attempt=attempt, created_by=actor_id,
        )
        db.add(snapshot)
        db.flush()
        failed = False
        connection = None
        try:
            if settings.execution_mode == "remote":
                connection = await asyncssh.connect(**_ssh_options(resource))
            for field in target.get("fields") or []:
                command = SERVER_COMMANDS[field]
                try:
                    if settings.execution_mode == "simulated":
                        value, raw, exit_code = SIMULATED_VALUES[field], SIMULATED_VALUES[field], 0
                    else:
                        result = await connection.run(command, check=False)
                        raw, exit_code = (result.stdout or result.stderr).strip(), result.exit_status
                        value = result.stdout.strip()
                        if exit_code != 0 or not value:
                            raise RuntimeError(result.stderr.strip() or "命令没有返回结果")
                    snapshot.items.append(ConfigurationCaptureItem(
                        item_key=field, item_label=FIELD_LABELS[field], value_text=value,
                        source_reference=command, raw_output=raw[:65535], exit_code=exit_code, status="succeeded",
                    ))
                except Exception as exc:
                    failed = True
                    snapshot.items.append(ConfigurationCaptureItem(
                        item_key=field, item_label=FIELD_LABELS[field], source_reference=command,
                        raw_output="", status="failed", error_message=str(exc),
                    ))
        except Exception as exc:
            failed = True
            snapshot.error_message = str(exc)
        finally:
            if connection:
                connection.close()
                with suppress(Exception):
                    await connection.wait_closed()
        snapshot.status = "failed" if failed else "succeeded"
        snapshot.finished_at = datetime.now(timezone.utc)
        snapshots.append(snapshot)
    db.flush()
    return snapshots


def _detect_setting_columns(columns: list[str]) -> tuple[str, str]:
    folded = {column.casefold(): column for column in columns}
    keys = [folded[item] for item in KEY_COLUMN_CANDIDATES if item in folded]
    values = [folded[item] for item in VALUE_COLUMN_CANDIDATES if item in folded]
    if len(keys) != 1 or len(values) != 1 or keys[0] == values[0]:
        raise WorkflowError("GLOBAL_SETTINGS_SCHEMA_UNKNOWN", "无法唯一识别 t_global_settings 的配置键和值列", 409)
    return keys[0], values[0]


async def capture_database(
    db: Session,
    scenario: TestScenario,
    version: ScenarioWorkflowVersion,
    node: ScenarioWorkflowNode,
    *,
    scope: str,
    actor_id: typing.Optional[int],
    run_id: typing.Optional[int] = None,
    run_step_id: typing.Optional[int] = None,
    run_resources: typing.Optional[dict[str, Resource]] = None,
) -> list[ConfigurationCaptureSnapshot]:
    resources = run_resources or resource_map(db, version)
    resource = resources.get("database")
    if not resource:
        raise WorkflowError("WORKFLOW_RESOURCE_MISSING", "运行资源缺少数据库", 409)
    database_name = validate_database(resource, str(node.config.get("database_name") or ""))
    keys = list(dict.fromkeys(node.config.get("keys") or []))
    attempt = (db.scalar(
        select(func.count(ConfigurationCaptureSnapshot.id)).where(
            ConfigurationCaptureSnapshot.workflow_node_id == node.id,
            ConfigurationCaptureSnapshot.scope == scope,
            ConfigurationCaptureSnapshot.resource_id == resource.id,
            ConfigurationCaptureSnapshot.run_id == run_id if run_id is not None
            else ConfigurationCaptureSnapshot.run_id.is_(None),
        )
    ) or 0) + 1
    snapshot = ConfigurationCaptureSnapshot(
        scenario_id=scenario.id, workflow_version_id=version.id, workflow_node_id=node.id,
        run_id=run_id, run_step_id=run_step_id, scope=scope, source_type="database",
        resource_id=resource.id, database_name=database_name, status="running",
        attempt=attempt, created_by=actor_id,
    )
    db.add(snapshot)
    db.flush()
    try:
        if settings.execution_mode == "simulated":
            values = {key: f"SIMULATED_{key}" for key in keys}
            source = f"{database_name}.t_global_settings"
        else:
            async with mysql_adapter.connection(resource, database_name) as connection:
                def query() -> tuple[dict[str, typing.Any], str]:
                    with connection.cursor() as cursor:
                        cursor.execute("SHOW COLUMNS FROM `t_global_settings`")
                        key_column, value_column = _detect_setting_columns([str(row[0]) for row in cursor.fetchall()])
                        placeholders = ",".join(["%s"] * len(keys))
                        sql = f"SELECT `{key_column}`, `{value_column}` FROM `t_global_settings` WHERE `{key_column}` IN ({placeholders})"
                        cursor.execute(sql, keys)
                        return {str(row[0]): row[1] for row in cursor.fetchall()}, f"{database_name}.t_global_settings.{key_column}/{value_column}"
                values, source = await to_thread(query)
        failed = False
        for key in keys:
            if key in values:
                snapshot.items.append(ConfigurationCaptureItem(
                    item_key=key, item_label=key, value_text=str(values[key]), source_reference=source,
                    raw_output=str(values[key]), exit_code=0, status="succeeded",
                ))
            else:
                failed = True
                snapshot.items.append(ConfigurationCaptureItem(
                    item_key=key, item_label=key, source_reference=source, raw_output="",
                    status="failed", error_message="配置项不存在",
                ))
        snapshot.status = "failed" if failed else "succeeded"
    except Exception as exc:
        snapshot.status = "failed"
        snapshot.error_message = str(exc)
    snapshot.finished_at = datetime.now(timezone.utc)
    db.flush()
    return [snapshot]


async def preview_node(db: Session, scenario: TestScenario, version: ScenarioWorkflowVersion, node: ScenarioWorkflowNode, actor_id: int) -> list[ConfigurationCaptureSnapshot]:
    if node.node_type == "server_config":
        return await capture_server(db, scenario, version, node, scope="preview", actor_id=actor_id)
    if node.node_type == "database_config":
        return await capture_database(db, scenario, version, node, scope="preview", actor_id=actor_id)
    raise WorkflowError("NODE_PREVIEW_NOT_SUPPORTED", "该节点不支持预采集", 409)


def parse_read_symbol_csv(document: dict) -> int:
    matches: list[str] = []
    def visit(node: dict) -> None:
        if str(node.get("name") or "").casefold() == "read_symbol_csv":
            attrs = {item.get("name"): item.get("value") for item in node.get("attributes") or []}
            value = attrs.get("value")
            if value is None:
                value = "".join(str(child.get("text") or "") for child in node.get("children") or [] if child.get("type") in {"text", "cdata"}).strip()
            matches.append(str(value))
        for child in node.get("children") or []:
            if child.get("type") == "element":
                visit(child)
    visit(document)
    if not matches:
        return 0
    if len(matches) != 1 or matches[0] not in {"0", "1"}:
        raise WorkflowError("READ_SYMBOL_CSV_INVALID", "XML 中 read_symbol_csv 必须唯一且值为 0 或 1", 409)
    return int(matches[0])


async def validate_publish(
    db: Session,
    scenario: TestScenario,
    version: ScenarioWorkflowVersion,
) -> tuple[list[dict], list[dict[str, typing.Any]]]:
    errors = validate_structure(db, scenario, version)
    resources = resource_map(db, version)
    order_config_updates: list[dict[str, typing.Any]] = []
    for node in version.nodes:
        if node.node_type != "order_preparation":
            continue
        config = node.config or {}
        resource = resources.get("order")
        if not resource or not config.get("xml_filename"):
            continue
        try:
            detail = await order_config_service.read(resource, config["xml_filename"])
            expected = str(config.get("xml_checksum") or "")
            if expected and expected != detail["checksum"]:
                raise WorkflowError("ORDER_CONFIG_CHANGED", "XML 配置已发生变化，请重新选择", 409)
            read_csv = parse_read_symbol_csv(detail["document"])
            config["xml_checksum"] = detail["checksum"]
            config["read_symbol_csv"] = read_csv
            if read_csv:
                file_ids = config.get("contract_file_ids") or []
                trading_database_name = str(config.get("trading_database_name") or "")
                database_resource = resources.get("database")
                if not database_resource or trading_database_name not in (database_resource.database_names or []):
                    raise WorkflowError("TRADING_DATABASE_INVALID", "交易数据库不在资源白名单中", 409)
                preceding = [
                    item for item in version.nodes
                    if item.position < node.position and item.node_type == "database_config"
                ]
                if not preceding:
                    raise WorkflowError("DATABASE_NODE_REQUIRED", "发单节点前需要数据库配置节点", 409)
                if not file_ids:
                    raise WorkflowError("CONTRACT_FILES_REQUIRED", "至少选择一个已归档的合约 CSV", 409)
                files = list(db.scalars(select(ContractDataFile).where(
                    ContractDataFile.id.in_(file_ids),
                )).all())
                if len(files) != len(set(file_ids)):
                    raise WorkflowError("CONTRACT_FILES_INVALID", "合约 CSV 不存在", 409)
                filenames: dict[str, str] = {}
                for item in files:
                    label = CONTRACT_TYPE_LABELS.get(item.contract_type)
                    if not label:
                        raise WorkflowError("CONTRACT_TYPE_INVALID", f"合约文件 {item.filename} 类型不受支持", 409)
                    if item.contract_type in filenames:
                        raise WorkflowError("CONTRACT_FILES_AMBIGUOUS", f"{label} CSV 只能选择一个", 409)
                    archive = Path(item.archive_path)
                    if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != item.checksum:
                        raise WorkflowError("CONTRACT_FILE_CHANGED", f"合约文件 {item.filename} 已丢失或校验失败", 409)
                    filenames[item.contract_type] = item.filename
                updated_content = update_symbol_csv_values(detail["content"], filenames)
                if updated_content != detail["content"]:
                    order_config_updates.append({
                        "node": node,
                        "resource": resource,
                        "filename": detail["name"],
                        "content": updated_content,
                        "expected_checksum": detail["checksum"],
                    })
            node.config = dict(config)
        except (OrderConfigError, WorkflowError) as exc:
            errors.append({"node_key": node.node_key, "field": "xml_filename", "message": str(exc)})
    return errors, order_config_updates


async def publish(db: Session, scenario: TestScenario, version: ScenarioWorkflowVersion, actor_id: int) -> ScenarioWorkflowVersion:
    errors, order_config_updates = await validate_publish(db, scenario, version)
    if errors:
        raise WorkflowError("WORKFLOW_VALIDATION_FAILED", "工作流校验未通过", 422, errors=errors)
    for item in order_config_updates:
        try:
            detail = await order_config_service.update(
                item["resource"],
                item["filename"],
                item["content"],
                item["expected_checksum"],
            )
        except OrderConfigError as exc:
            node = item["node"]
            raise WorkflowError(
                "WORKFLOW_VALIDATION_FAILED",
                "工作流校验未通过",
                422,
                errors=[{"node_key": node.node_key, "field": "xml_filename", "message": str(exc)}],
            ) from exc
        node = item["node"]
        config = dict(node.config or {})
        config["xml_checksum"] = detail["checksum"]
        node.config = config
    if scenario.published_workflow_version_id:
        previous = db.get(ScenarioWorkflowVersion, scenario.published_workflow_version_id)
        if previous:
            previous.status = "retired"
    version.status = "published"
    version.published_by = actor_id
    version.published_at = datetime.now(timezone.utc)
    scenario.published_workflow_version_id = version.id
    scenario.draft_workflow_version_id = None
    scenario.workflow_status = "published"
    scenario.is_enabled = True
    scenario.is_archived = False
    scenario.default_resource_ids = list(version.resource_ids)
    scenario.required_resource_types = sorted(resource_map(db, version))
    db.flush()
    return version


@asynccontextmanager
async def _sftp(resource: Resource):
    connection = await asyncssh.connect(**_ssh_options(resource))
    try:
        client = await connection.start_sftp_client()
        try:
            yield client
        finally:
            client.exit()
    finally:
        connection.close()
        with suppress(Exception):
            await connection.wait_closed()


async def _write_remote_contract(resource: Resource, filename: str, source: Path) -> str:
    remote_path = posixpath.join(resource.remote_path.rstrip("/"), filename)
    if settings.execution_mode == "simulated":
        return remote_path
    temporary = posixpath.join(resource.remote_path.rstrip("/"), f".openslt-{uuid4().hex}.tmp")
    async with _sftp(resource) as client:
        try:
            await client.put(str(source), temporary)
            await client.posix_rename(temporary, remote_path)
        finally:
            with suppress(asyncssh.SFTPError):
                await client.remove(temporary)
    return remote_path


async def _export_contract_csv(
    database_resource: Resource,
    database_name: str,
    table: str,
    target: Path,
) -> tuple[str, int, list[dict[str, typing.Any]]]:
    if settings.execution_mode == "simulated":
        quote_date = datetime.now(timezone.utc).date().isoformat()
        rows = [
            {"quote_date": quote_date, "symbol": f"SIM{index:04d}", "exchange": "SIM"}
            for index in range(1, 7)
        ]
        with target.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return quote_date, len(rows), rows[:5]

    try:
        async with mysql_adapter.connection(database_resource, database_name) as connection:
            def export() -> tuple[str, int, list[dict[str, typing.Any]]]:
                sql = f"SELECT * FROM `{table}` WHERE `quote_date` = (SELECT MAX(`quote_date`) FROM `{table}`)"
                with connection.cursor(SSCursor) as cursor, target.open(
                    "w", encoding="utf-8-sig", newline=""
                ) as output:
                    cursor.execute(sql)
                    columns = [item[0] for item in cursor.description or []]
                    if "quote_date" not in columns:
                        raise WorkflowError("CONTRACT_DATE_COLUMN_MISSING", f"{table} 缺少 quote_date 字段", 409)
                    writer = csv.writer(output)
                    writer.writerow(columns)
                    row_count = 0
                    quote_date = ""
                    preview_rows: list[dict[str, typing.Any]] = []
                    while True:
                        batch = cursor.fetchmany(1000)
                        if not batch:
                            break
                        writer.writerows(batch)
                        for values in batch:
                            row = dict(zip(columns, values))
                            if not quote_date:
                                quote_date = str(row.get("quote_date") or "")
                            if len(preview_rows) < 5:
                                preview_rows.append(row)
                        row_count += len(batch)
                    return quote_date, row_count, preview_rows

            quote_date, row_count, preview_rows = await to_thread(export)
    except WorkflowError:
        raise
    except Exception as exc:
        raise WorkflowError("CONTRACT_EXPORT_FAILED", f"{table} 导出失败: {exc}", 409) from exc
    if not row_count:
        raise WorkflowError("CONTRACT_DATA_EMPTY", f"{table} 没有可导出的数据", 409)
    return quote_date, row_count, preview_rows


async def fetch_contract_files(
    db: Session,
    scenario: TestScenario,
    version: ScenarioWorkflowVersion,
    node: ScenarioWorkflowNode,
    database_resource: Resource,
    database_name: str,
    contract_types: list[str],
    actor_id: int,
) -> list[ContractDataFile]:
    if node.node_type != "order_preparation":
        raise WorkflowError("ORDER_NODE_REQUIRED", "只有发单节点可以获取合约数据", 400)
    resources = resource_map(db, version)
    order_resource = resources.get("order")
    if not order_resource:
        raise WorkflowError("ORDER_RESOURCE_REQUIRED", "场景资源池缺少发单工具", 409)
    database_name = validate_database(database_resource, database_name)
    archive_dir = settings.artifact_root / "workflows" / str(scenario.id) / str(version.id) / node.node_key / "contracts"
    archive_dir.mkdir(parents=True, exist_ok=True)
    created: list[ContractDataFile] = []
    for contract_type in list(dict.fromkeys(contract_types)):
        table = CONTRACT_TABLES.get(contract_type)
        if not table:
            raise WorkflowError("CONTRACT_TYPE_INVALID", "合约类型不受支持")
        handle, temporary_name = tempfile.mkstemp(
            prefix=".openslt-contract-", suffix=".csv", dir=str(archive_dir)
        )
        os.close(handle)
        temporary = Path(temporary_name)
        try:
            quote_date, row_count, preview_rows = await _export_contract_csv(
                database_resource, database_name, table, temporary
            )
            safe_date = re.sub(r"[^0-9]", "", quote_date) or "unknown"
            filename = f"{table}_{safe_date}.csv"
            archive_path = archive_dir / filename
            data = temporary.read_bytes()
            checksum = hashlib.sha256(data).hexdigest()
            if archive_path.exists() and hashlib.sha256(archive_path.read_bytes()).hexdigest() != checksum:
                archive_path = archive_dir / f"{table}_{safe_date}_{checksum[:8]}.csv"
            temporary.replace(archive_path)
            remote_path = await _write_remote_contract(order_resource, archive_path.name, archive_path)
        finally:
            temporary.unlink(missing_ok=True)
        existing = db.scalar(select(ContractDataFile).where(
            ContractDataFile.workflow_node_id == node.id,
            ContractDataFile.filename == archive_path.name,
            ContractDataFile.checksum == checksum,
        ))
        if existing:
            created.append(existing)
            continue
        item = ContractDataFile(
            scenario_id=scenario.id, workflow_node_id=node.id, order_resource_id=order_resource.id,
            database_resource_id=database_resource.id, database_name=database_name,
            contract_type=contract_type, source_table=table, filename=archive_path.name,
            remote_path=remote_path, archive_path=str(archive_path), quote_date=quote_date,
            row_count=row_count, size=archive_path.stat().st_size, checksum=checksum,
            preview_rows=[{key: str(value) if value is not None else None for key, value in row.items()} for row in preview_rows],
            created_by=actor_id,
        )
        db.add(item)
        created.append(item)
    db.flush()
    return created


async def prepare_order_node(
    db: Session,
    version: ScenarioWorkflowVersion,
    node: ScenarioWorkflowNode,
    run_resources: dict[str, Resource],
) -> dict:
    resource = run_resources.get("order")
    if not resource:
        raise WorkflowError("ORDER_RESOURCE_REQUIRED", "运行资源缺少发单工具", 409)
    config = node.config or {}
    try:
        detail = await order_config_service.read(resource, str(config.get("xml_filename") or ""))
    except OrderConfigError as exc:
        raise WorkflowError(exc.code, exc.message, exc.status_code) from exc
    expected = str(config.get("xml_checksum") or "")
    if expected and detail["checksum"] != expected:
        raise WorkflowError("ORDER_CONFIG_CHANGED", "XML 配置校验值与发布版本不一致", 409)
    read_csv = parse_read_symbol_csv(detail["document"])
    file_summaries = []
    if read_csv:
        file_ids = list(config.get("contract_file_ids") or [])
        files = list(db.scalars(select(ContractDataFile).where(ContractDataFile.id.in_(file_ids))).all())
        if len(files) != len(set(file_ids)):
            raise WorkflowError("CONTRACT_FILES_INVALID", "合约 CSV 不存在", 409)
        for item in files:
            archive = Path(item.archive_path)
            if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != item.checksum:
                raise WorkflowError("CONTRACT_FILE_CHANGED", f"合约文件 {item.filename} 已丢失或校验失败", 409)
            remote_path = await _write_remote_contract(resource, item.filename, archive)
            file_summaries.append({
                "id": item.id, "filename": item.filename, "remote_path": remote_path,
                "quote_date": item.quote_date, "row_count": item.row_count, "checksum": item.checksum,
            })
    interface = str(config.get("network_interface") or "")
    if interface and not INTERFACE_PATTERN.fullmatch(interface):
        raise WorkflowError("NETWORK_INTERFACE_INVALID", "网卡接口名称不合法", 409)
    binary = (resource.capabilities or {}).get("order_tool") or posixpath.basename(resource.remote_path.rstrip("/"))
    command_parts = [f"cd {shlex.quote(resource.remote_path)}"]
    if interface:
        command_parts.append(f"export ZF_ATTR=interface={interface}")
    command_parts.append(f"{shlex.quote('./' + binary)} {shlex.quote(detail['name'])}")
    return {
        "prepared": True,
        "xml_filename": detail["name"],
        "xml_checksum": detail["checksum"],
        "read_symbol_csv": read_csv,
        "network_interface": interface or None,
        "contract_files": file_summaries,
        "generated_command": " && ".join(command_parts),
        "process_started": False,
    }


def _slnic_artifact_path(run: TestRun, step: RunStep) -> Path:
    return (
        settings.artifact_root
        / run.business_code
        / str(run.plan_id)
        / str(run.scenario_id)
        / run.run_number
        / "slnic"
        / str(step.id)
        / "merge_pcap.pcapng"
    )


def _write_simulated_pcapng(target: Path) -> None:
    """Write a minimal valid little-endian pcapng section header block."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(
            struct.pack("<IIIHHqI", 0x0A0D0D0A, 28, 0x1A2B3C4D, 1, 0, -1, 28)
        )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


async def _run_slnic_command(connection: typing.Any, command: str, label: str) -> None:
    result = await connection.run(command, check=False)
    if result.exit_status == 0:
        return
    detail = str(result.stderr or result.stdout or "远端命令没有返回错误信息").strip()[:1000]
    raise WorkflowError(
        "SLNIC_COMMAND_FAILED",
        f"{label}失败（退出码 {result.exit_status}）：{detail}",
        409,
    )


async def execute_slnic_node(
    db: Session,
    run: TestRun,
    step: RunStep,
    node: ScenarioWorkflowNode,
    run_resources: dict[str, Resource],
) -> dict:
    resource = run_resources.get("slnic")
    if not resource or resource.is_deleted or not resource.is_enabled:
        raise WorkflowError("SLNIC_RESOURCE_REQUIRED", "运行资源缺少已启用的 SLNIC 节点", 409)
    if node.node_type not in SLNIC_NODE_TYPES:
        raise WorkflowError("SLNIC_NODE_REQUIRED", "当前节点不是 SLNIC 节点", 400)
    if not resource.remote_path.strip():
        raise WorkflowError("SLNIC_REMOTE_PATH_REQUIRED", "SLNIC 资源未配置远端路径", 409)

    mode = settings.execution_mode
    summary = {"resource_id": resource.id, "mode": mode, "exit_code": 0}
    target = _slnic_artifact_path(run, step)
    if mode == "simulated":
        if node.node_type != "slnic_merge_capture":
            return summary
        _write_simulated_pcapng(target)
    else:
        workdir = posixpath.join(resource.remote_path.rstrip("/"), "tcpdump")
        prefix = f"cd {shlex.quote(workdir)} && "
        connection = None
        sftp = None
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.part")
        try:
            connection = await asyncssh.connect(**_ssh_options(resource))
            if node.node_type == "slnic_start_capture":
                await _run_slnic_command(
                    connection, prefix + "./start_slnic_dump.sh", "启动 SLNIC 抓包"
                )
                return summary
            if node.node_type == "slnic_stop_capture":
                await _run_slnic_command(
                    connection, prefix + "./stop_slnic_dump.sh", "关闭 SLNIC 抓包"
                )
                return summary

            await _run_slnic_command(
                connection, prefix + "./pcap_mergetoo slnic*", "合并 SLNIC 抓包"
            )
            await _run_slnic_command(
                connection,
                prefix
                + "if [ ! -f merge_pcap.pcap ] && [ -f merge_pacp.pcap ]; "
                + "then mv -- merge_pacp.pcap merge_pcap.pcap; fi; "
                + "test -f merge_pcap.pcap",
                "检查合并后的 pcap 文件",
            )
            await _run_slnic_command(
                connection,
                prefix + "./editcap merge_pcap.pcap merge_pcap.pcapng && test -f merge_pcap.pcapng",
                "转换 pcapng 文件",
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            sftp = await connection.start_sftp_client()
            remote_file = posixpath.join(workdir, "merge_pcap.pcapng")
            await sftp.get(remote_file, str(temporary))
            temporary.replace(target)
        except WorkflowError:
            raise
        except Exception as exc:
            raise WorkflowError("SLNIC_EXECUTION_FAILED", f"SLNIC 节点执行失败：{exc}", 409) from exc
        finally:
            temporary.unlink(missing_ok=True)
            if sftp:
                sftp.exit()
            if connection:
                connection.close()
                with suppress(Exception):
                    await connection.wait_closed()

    data = target.read_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    artifact = db.scalar(
        select(Artifact).where(
            Artifact.run_id == run.id,
            Artifact.step_id == step.id,
            Artifact.name == target.name,
        )
    )
    if artifact is None:
        artifact = Artifact(
            run_id=run.id,
            step_id=step.id,
            artifact_type="packet_capture",
            name=target.name,
            path=str(target),
        )
        db.add(artifact)
    artifact.artifact_type = "packet_capture"
    artifact.path = str(target)
    artifact.content_type = "application/vnd.tcpdump.pcap"
    artifact.size = len(data)
    artifact.checksum = checksum
    artifact.is_immutable = True
    db.flush()
    summary.update(
        {
            "artifact_id": artifact.id,
            "filename": artifact.name,
            "checksum": artifact.checksum,
            "size": artifact.size,
        }
    )
    return summary

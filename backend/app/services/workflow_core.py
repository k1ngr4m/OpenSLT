from __future__ import annotations

import re
import typing
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Resource, ScenarioWorkflowNode, ScenarioWorkflowVersion, TestScenario
from app.services.order_configs import parser_main_config_filename
from app.services.resource_relations import (
    node_config_with_relations,
    node_contract_file_ids,
    scenario_resource_ids,
    sync_node_contract_files,
    sync_scenario_resources,
    sync_workflow_resources,
    workflow_resource_ids,
)
from app.wiring_profiles import build_wiring_snapshot

SLNIC_NODE_TYPES = {"slnic_start_capture", "slnic_stop_capture", "slnic_merge_capture"}
NODE_TYPES = {
    "server_config",
    "database_config",
    "wiring_confirmation",
    "order_preparation",
    "parser_parse",
    *SLNIC_NODE_TYPES,
}
PARSER_TABLES = ("t_fut_orders", "t_fut_quotes", "t_fut_arbi_orders")
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
        .options(
            selectinload(ScenarioWorkflowVersion.nodes).selectinload(
                ScenarioWorkflowNode.contract_file_links
            )
        )
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
        created_by=actor_id,
    )
    sync_workflow_resources(draft, resource_ids)
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
        return create_draft(db, scenario, actor_id, scenario_resource_ids(scenario))
    published = load_version(db, scenario.published_workflow_version_id)
    draft = create_draft(db, scenario, actor_id, workflow_resource_ids(published))
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
        config = node_config_with_relations(source_node)
        target_node = ScenarioWorkflowNode(
            node_key=str(uuid4()), position=source_node.position, node_type=source_node.node_type,
            name=source_node.name, config=config,
        )
        sync_node_contract_files(target_node, config.get("contract_file_ids") or [])
        target.nodes.append(target_node)
    db.flush()


def resource_map(db: Session, version: ScenarioWorkflowVersion) -> dict[str, Resource]:
    resource_ids = workflow_resource_ids(version)
    resources = list(db.scalars(select(Resource).where(Resource.id.in_(resource_ids))).all()) if resource_ids else []
    return {item.resource_type: item for item in resources}


def validate_structure(db: Session, scenario: TestScenario, version: ScenarioWorkflowVersion) -> list[dict]:
    errors: list[dict] = []
    resources = resource_map(db, version)
    slnic_state = "idle"
    if not version.nodes:
        errors.append({"field": "nodes", "message": "主流程至少需要一个节点"})
    for node in version.nodes:
        config = node_config_with_relations(node)
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
        elif node.node_type == "wiring_confirmation":
            if str(config.get("diagram") or "placeholder") == "resource":
                rem_resource = resources.get("rem")
                market_resource = resources.get("market")
                slnic_resource = resources.get("slnic")
                if not rem_resource:
                    errors.append({**prefix, "field": "resource", "message": "接线确认需要绑定 REM 柜台"})
                if not market_resource:
                    errors.append({**prefix, "field": "resource", "message": "接线确认需要绑定模拟市场"})
                if not slnic_resource:
                    errors.append({**prefix, "field": "resource", "message": "接线确认需要绑定 SLNIC 节点"})
                if rem_resource and market_resource and slnic_resource:
                    try:
                        build_wiring_snapshot(
                            rem_resource, market_resource, slnic_resource, scenario.plan.business_code
                        )
                    except (KeyError, ValueError):
                        errors.append({
                            **prefix,
                            "field": "resource",
                            "message": "REM、模拟市场或 SLNIC 的接线 IP 配置无效",
                        })
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
                if not node_contract_file_ids(node):
                    errors.append({**prefix, "field": "contract_file_ids", "message": "至少选择一个合约 CSV"})
                preceding = [item for item in version.nodes if item.position < node.position and item.node_type == "database_config"]
                if not preceding:
                    errors.append({**prefix, "field": "database_node_key", "message": "发单节点前需要数据库配置节点"})
        elif node.node_type == "parser_parse":
            parser_resource = resources.get("parser")
            database_resource = resources.get("database")
            database_name = str(config.get("database_name") or "").strip()
            if not parser_resource or parser_resource.is_deleted or not parser_resource.is_enabled:
                errors.append({**prefix, "field": "resource", "message": "场景资源池缺少已启用的解析工具资源"})
            else:
                capabilities = parser_resource.capabilities or {}
                if not str(capabilities.get("parser_binary") or capabilities.get("parser_tool") or "").strip():
                    errors.append({**prefix, "field": "resource", "message": "解析工具资源未配置可执行文件"})
                if not parser_resource.remote_path.strip():
                    errors.append({**prefix, "field": "resource", "message": "解析工具资源未配置远端路径"})
                if not parser_main_config_filename(parser_resource).strip():
                    errors.append({**prefix, "field": "resource", "message": "解析工具资源未配置主 XML"})
            if not database_resource:
                errors.append({**prefix, "field": "resource", "message": "场景资源池缺少数据库资源"})
            elif database_name not in (database_resource.database_names or []):
                errors.append({**prefix, "field": "database_name", "message": "运行数据库不在资源白名单中"})
            preceding_merges = [
                item for item in version.nodes
                if item.position < node.position and item.node_type == "slnic_merge_capture"
            ]
            if not preceding_merges or slnic_state != "merged":
                errors.append({**prefix, "field": "position", "message": "数据解析前需要先完成 SLNIC 合并 pcapng 节点"})
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
    sync_workflow_resources(version, resource_ids, db)
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
            config = dict(item.get("config") or {})
            node.config = config
            sync_node_contract_files(node, config.get("contract_file_ids") or [], db)
        else:
            config = dict(item.get("config") or {})
            node = ScenarioWorkflowNode(
                node_key=item["node_key"],
                position=position,
                node_type=item["node_type"],
                name=item["name"].strip(),
                config=config,
            )
            sync_node_contract_files(node, config.get("contract_file_ids") or [])
            version.nodes.append(node)
    version.revision += 1
    sync_scenario_resources(scenario, resource_ids, db)
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
            "resource_ids": workflow_resource_ids(version),
            "published_by": version.published_by,
            "published_at": version.published_at,
            "created_at": version.created_at,
            "updated_at": version.updated_at,
            "nodes": [
                {"id": node.id, "node_key": node.node_key, "position": node.position, "node_type": node.node_type, "name": node.name, "config": node_config_with_relations(node)}
                for node in version.nodes
            ],
        },
        "published_version_id": scenario.published_workflow_version_id,
        "validation_errors": errors,
    }

from __future__ import annotations

import re
import typing
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Resource, ScenarioWorkflowNode, ScenarioWorkflowVersion, TestScenario
from app.services.order_configs import parser_config_role, parser_main_config_filename
from app.services.parser_inputs import parser_config_database_name
from app.services.resource_relations import (
    node_config_with_relations,
    node_contract_file_ids,
    scenario_resource_ids,
    sync_node_contract_files,
    sync_scenario_resources,
    sync_workflow_resources,
    workflow_resource_ids,
)
from app.wiring_profiles import build_wiring_snapshot, wiring_interface_names
from app.workflow_node_configs import ORDER_ACTIONS, REM_STARTUP_DEFAULT_COMMANDS

SLNIC_NODE_TYPES = {"slnic_start_capture", "slnic_stop_capture", "slnic_merge_capture"}
NODE_TYPES = {
    "server_config",
    "database_config",
    "wiring_confirmation",
    "rem_startup",
    "market_startup",
    "order_preparation",
    "parser_parse",
    "data_statistics",
    "report_generation",
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
        generation_no=1,
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
    next_generation = (
        db.scalar(
            select(func.max(ScenarioWorkflowVersion.generation_no)).where(
                ScenarioWorkflowVersion.scenario_id == scenario.id,
                ScenarioWorkflowVersion.version_no == published.version_no,
            )
        )
        or 0
    ) + 1
    draft = ScenarioWorkflowVersion(
        scenario_id=scenario.id,
        version_no=published.version_no,
        generation_no=next_generation,
        status="draft",
        revision=1,
        created_by=actor_id,
    )
    sync_workflow_resources(draft, workflow_resource_ids(published))
    db.add(draft)
    db.flush()
    scenario.draft_workflow_version_id = draft.id
    scenario.workflow_status = "draft"
    scenario.is_enabled = False
    copy_version_contents(db, published, draft, actor_id)
    return draft


def create_next_version(
    db: Session,
    scenario: TestScenario,
    source: ScenarioWorkflowVersion,
    actor_id: int,
) -> ScenarioWorkflowVersion:
    if scenario.draft_workflow_version_id:
        raise WorkflowError("WORKFLOW_DRAFT_EXISTS", "已有设计中的流程版本", 409)
    if source.scenario_id != scenario.id:
        raise WorkflowError("WORKFLOW_NOT_FOUND", "工作流版本不存在", 404)
    draft = create_draft(db, scenario, actor_id, workflow_resource_ids(source))
    copy_version_contents(db, source, draft, actor_id)
    if scenario.published_workflow_version_id:
        published = db.get(ScenarioWorkflowVersion, scenario.published_workflow_version_id)
        if published and published.status == "published":
            published.status = "retired"
    scenario.is_enabled = False
    scenario.workflow_status = "draft"
    return draft


def version_heads_query(scenario_id: int):
    latest = (
        select(
            ScenarioWorkflowVersion.version_no.label("version_no"),
            func.max(ScenarioWorkflowVersion.generation_no).label("generation_no"),
        )
        .where(ScenarioWorkflowVersion.scenario_id == scenario_id)
        .group_by(ScenarioWorkflowVersion.version_no)
        .subquery()
    )
    return (
        select(ScenarioWorkflowVersion)
        .join(
            latest,
            (ScenarioWorkflowVersion.version_no == latest.c.version_no)
            & (ScenarioWorkflowVersion.generation_no == latest.c.generation_no),
        )
        .where(ScenarioWorkflowVersion.scenario_id == scenario_id)
    )


def is_version_head(db: Session, version: ScenarioWorkflowVersion) -> bool:
    latest_generation = db.scalar(
        select(func.max(ScenarioWorkflowVersion.generation_no)).where(
            ScenarioWorkflowVersion.scenario_id == version.scenario_id,
            ScenarioWorkflowVersion.version_no == version.version_no,
        )
    )
    return version.generation_no == latest_generation


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
    if not version.nodes:
        errors.append({"field": "nodes", "message": "主流程至少需要一个节点"})
    report_nodes = [item for item in version.nodes if item.node_type == "report_generation"]
    if len(report_nodes) > 1:
        for report_node in report_nodes[1:]:
            errors.append({
                "node_key": report_node.node_key,
                "field": "node_type",
                "message": "每个工作流最多只能有一个报告生成节点",
            })
    if len(report_nodes) == 1:
        report_node = report_nodes[0]
        ordered_nodes = sorted(version.nodes, key=lambda item: (item.position, item.id or 0))
        if ordered_nodes[-1] is not report_node:
            errors.append({
                "node_key": report_node.node_key,
                "field": "position",
                "message": "报告生成节点必须位于工作流末尾",
            })
        if not any(
            item.node_type == "data_statistics" and item.position < report_node.position
            for item in version.nodes
        ):
            errors.append({
                "node_key": report_node.node_key,
                "field": "position",
                "message": "报告生成节点前至少需要一个数据统计节点",
            })
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
            if not keys:
                errors.append({**prefix, "field": "keys", "message": "至少选择一个配置项"})
            elif len(keys) > 1000:
                errors.append({**prefix, "field": "keys", "message": "配置项不能超过 1000 个"})
            elif len(keys) != len(set(keys)):
                errors.append({**prefix, "field": "keys", "message": "配置项不能重复"})
            elif any(not isinstance(key, str) or not key.strip() or len(key) > 255 for key in keys):
                errors.append({**prefix, "field": "keys", "message": "配置项格式无效"})
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
                try:
                    client_name, market_name, auxiliary_names = wiring_interface_names(
                        scenario.plan.business_code,
                        client_interface_name=config.get("client_interface_name"),
                        market_interface_name=config.get("market_interface_name"),
                        auxiliary_interface_names=config.get("auxiliary_interface_names"),
                    )
                    required_names = [client_name, market_name]
                    if scenario.plan.business_code != "fut_mm":
                        required_names.extend(auxiliary_names)
                        if len(auxiliary_names) != 2:
                            errors.append({
                                **prefix,
                                "field": "auxiliary_interface_names",
                                "message": "整合版接线图需要配置第 3、4 个接口名称",
                            })
                    if any(not name.strip() for name in required_names):
                        errors.append({
                            **prefix,
                            "field": "interface_names",
                            "message": "接线图接口名称不能为空",
                        })
                except KeyError:
                    errors.append({
                        **prefix,
                        "field": "interface_names",
                        "message": "接线图业务类型不受支持",
                    })
                if rem_resource and market_resource and slnic_resource:
                    try:
                        build_wiring_snapshot(
                            rem_resource,
                            market_resource,
                            slnic_resource,
                            scenario.plan.business_code,
                            client_interface_name=config.get("client_interface_name"),
                            market_interface_name=config.get("market_interface_name"),
                            auxiliary_interface_names=config.get("auxiliary_interface_names"),
                        )
                    except (KeyError, ValueError):
                        errors.append({
                            **prefix,
                            "field": "resource",
                            "message": "REM、模拟市场或 SLNIC 的接线 IP 配置无效",
                        })
        elif node.node_type == "rem_startup":
            rem_resource = resources.get("rem")
            commands = config.get("commands", list(REM_STARTUP_DEFAULT_COMMANDS))
            if not rem_resource or rem_resource.is_deleted or not rem_resource.is_enabled:
                errors.append({**prefix, "field": "resource", "message": "启动 REM 柜台需要绑定已启用的 REM 资源"})
            elif not rem_resource.remote_path.strip():
                errors.append({**prefix, "field": "resource", "message": "REM 资源未配置远端路径"})
            if not commands:
                errors.append({**prefix, "field": "commands", "message": "启动 REM 柜台至少需要一条命令"})
        elif node.node_type == "market_startup":
            market_resource = resources.get("market")
            scripts = config.get("scripts") or []
            if not market_resource or market_resource.is_deleted or not market_resource.is_enabled:
                errors.append({**prefix, "field": "resource", "message": "启动模拟市场需要绑定已启用的模拟市场资源"})
            elif not market_resource.remote_path.strip():
                errors.append({**prefix, "field": "resource", "message": "模拟市场资源未配置远端路径"})
            if not scripts:
                errors.append({**prefix, "field": "scripts", "message": "至少选择一个模拟市场启动脚本"})
        elif node.node_type == "order_preparation":
            order_resource = resources.get("order")
            if not order_resource:
                errors.append({**prefix, "field": "resource", "message": "场景资源池缺少发单工具资源"})
            else:
                supported_actions = (order_resource.capabilities or {}).get("order_actions") or ORDER_ACTIONS
                if config.get("order_action", "new_order") not in supported_actions:
                    errors.append({**prefix, "field": "order_action", "message": "发单资源不支持所选动作"})
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
            parser_xml_fields = (
                ("config", "config_xml_filename", "config.xml 配置"),
                ("instance", "instance_xml_filename", "instance.xml 配置"),
                ("analysis", "analysis_xml_filename", "分析主配置"),
            )
            selected_parser_xml: set[str] = set()
            for expected_role, field, label in parser_xml_fields:
                filename = str(config.get(field) or "").strip()
                checksum = str(config.get(field.replace("filename", "checksum")) or "").strip()
                if not filename:
                    errors.append({**prefix, "field": field, "message": f"请选择{label}"})
                    continue
                if parser_config_role(filename) != expected_role:
                    errors.append({**prefix, "field": field, "message": f"{label}文件类型不正确"})
                if filename in selected_parser_xml:
                    errors.append({**prefix, "field": field, "message": "三个解析 XML 不能重复"})
                selected_parser_xml.add(filename)
                if not re.fullmatch(r"[0-9a-f]{64}", checksum):
                    errors.append({**prefix, "field": field.replace("filename", "checksum"), "message": f"请重新选择{label}以固化校验和"})
            if not database_resource:
                errors.append({**prefix, "field": "resource", "message": "场景资源池缺少数据库资源"})
            elif database_name not in (database_resource.database_names or []):
                errors.append({**prefix, "field": "database_name", "message": "运行数据库不在资源白名单中"})
            else:
                config_database_name = parser_config_database_name(database_name)
                if not config_database_name:
                    errors.append({
                        **prefix,
                        "field": "database_name",
                        "message": "运行数据库名称必须以 _trading_data 结尾",
                    })
                elif config_database_name not in (database_resource.database_names or []):
                    errors.append({
                        **prefix,
                        "field": "database_name",
                        "message": f"数据库资源缺少配套配置库 {config_database_name}",
                    })
        elif node.node_type == "data_statistics":
            parser_resource = resources.get("parser")
            if not parser_resource or parser_resource.is_deleted or not parser_resource.is_enabled:
                errors.append({**prefix, "field": "resource", "message": "数据统计需要已启用的解析工具资源"})
            if not re.fullmatch(r"[A-Za-z0-9._-]+\.py", str(config.get("script_filename") or "")):
                errors.append({**prefix, "field": "script_filename", "message": "请选择有效的远端统计脚本"})
            if not re.fullmatch(r"[0-9a-f]{64}", str(config.get("script_checksum") or "")):
                errors.append({**prefix, "field": "script_checksum", "message": "请重新选择统计脚本以固化校验和"})
            if not isinstance(config.get("max_latency_ns"), int) or int(config.get("max_latency_ns") or 0) < 1:
                errors.append({**prefix, "field": "max_latency_ns", "message": "异常大值上限必须为正整数"})
        elif node.node_type in SLNIC_NODE_TYPES:
            resource = resources.get("slnic")
            if not resource or resource.is_deleted or not resource.is_enabled:
                errors.append({**prefix, "field": "resource", "message": "场景资源池缺少已启用的 SLNIC 资源"})
            elif not resource.remote_path.strip():
                errors.append({**prefix, "field": "resource", "message": "SLNIC 资源未配置远端路径"})
            commands = config.get("commands")
            if commands is not None and not commands:
                errors.append({**prefix, "field": "commands", "message": "SLNIC 节点至少需要一条命令"})
            if node.node_type == "slnic_merge_capture":
                parser_resource = resources.get("parser")
                if (
                    not parser_resource
                    or parser_resource.is_deleted
                    or not parser_resource.is_enabled
                ):
                    errors.append({
                        **prefix,
                        "field": "parser_resource",
                        "message": "合并 pcapng 需要绑定已启用的解析工具资源",
                    })
                elif not parser_resource.remote_path.strip().startswith("/home/"):
                    errors.append({
                        **prefix,
                        "field": "parser_resource",
                        "message": "解析工具远端路径必须位于 /home/ 下",
                    })
                if resource and not resource.remote_path.strip().startswith("/home/"):
                    errors.append({
                        **prefix,
                        "field": "resource",
                        "message": "SLNIC 远端路径必须位于 /home/ 下",
                    })
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
            version.nodes.remove(node)
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

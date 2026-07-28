from __future__ import annotations

import hashlib
import typing
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import beijing_now
from app.models import ContractDataFile, ScenarioWorkflowVersion, TestScenario
from app.services.order_configs import OrderConfigError, order_config_service, update_symbol_csv_values
from app.services.resource_relations import node_config_with_relations, node_contract_file_ids, sync_scenario_resources, workflow_resource_ids
from app.services.statistics_scripts import StatisticsScriptError, statistics_script_service
from app.services.workflow_contracts import parse_read_symbol_csv
from app.services.workflow_core import CONTRACT_TYPE_LABELS, WorkflowError, resource_map, validate_structure

async def validate_publish(
    db: Session,
    scenario: TestScenario,
    version: ScenarioWorkflowVersion,
) -> tuple[list[dict], list[dict[str, typing.Any]]]:
    errors = validate_structure(db, scenario, version)
    resources = resource_map(db, version)
    order_config_updates: list[dict[str, typing.Any]] = []
    for node in version.nodes:
        if node.node_type == "data_statistics":
            resource = resources.get("parser")
            config = node_config_with_relations(node)
            filename = str(config.get("script_filename") or "").strip()
            expected = str(config.get("script_checksum") or "").strip()
            if resource and filename and expected:
                try:
                    detail = await statistics_script_service.read(resource, filename)
                    if not detail["executable"]:
                        raise WorkflowError("STATISTICS_SCRIPT_NOT_EXECUTABLE", "统计脚本没有可执行权限", 409)
                    if detail["checksum"] != expected:
                        raise WorkflowError("STATISTICS_SCRIPT_CHANGED", "统计脚本已发生变化，请重新选择", 409)
                except (StatisticsScriptError, WorkflowError) as exc:
                    errors.append({"node_key": node.node_key, "field": "script_filename", "message": str(exc)})
            continue
        if node.node_type == "parser_parse":
            resource = resources.get("parser")
            if resource:
                config = node_config_with_relations(node)
                parser_xml_fields = (
                    ("config_xml_filename", "config_xml_checksum", "config.xml 配置"),
                    ("instance_xml_filename", "instance_xml_checksum", "instance.xml 配置"),
                    ("analysis_xml_filename", "analysis_xml_checksum", "分析主配置"),
                )
                for filename_field, checksum_field, label in parser_xml_fields:
                    filename = str(config.get(filename_field) or "").strip()
                    expected = str(config.get(checksum_field) or "").strip()
                    if not filename or not expected:
                        continue
                    try:
                        detail = await order_config_service.read(resource, filename)
                        if detail["checksum"] != expected:
                            raise WorkflowError("PARSER_CONFIG_CHANGED", f"{label}已发生变化，请重新选择", 409)
                    except (OrderConfigError, WorkflowError) as exc:
                        errors.append({"node_key": node.node_key, "field": filename_field, "message": str(exc)})
            continue
        if node.node_type != "order_preparation":
            continue
        config = node_config_with_relations(node)
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
                file_ids = node_contract_file_ids(node)
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
        config = node_config_with_relations(node)
        config["xml_checksum"] = detail["checksum"]
        node.config = config
    if scenario.published_workflow_version_id:
        previous = db.get(ScenarioWorkflowVersion, scenario.published_workflow_version_id)
        if previous:
            previous.status = "retired"
    version.status = "published"
    version.published_by = actor_id
    version.published_at = beijing_now()
    scenario.published_workflow_version_id = version.id
    scenario.draft_workflow_version_id = None
    scenario.workflow_status = "published"
    scenario.is_enabled = True
    scenario.is_archived = False
    sync_scenario_resources(scenario, workflow_resource_ids(version), db)
    scenario.required_resource_types = sorted(resource_map(db, version))
    db.flush()
    return version

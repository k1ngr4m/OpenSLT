from __future__ import annotations

import typing

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.adapters.database import DatabaseOperationError, validate_database
from app.models import ContractDataFile, Resource, ScenarioWorkflowVersion, TestPlan, TestRun
from app.services.order_configs import OrderConfigError
from app.services.workflows import WorkflowError


def not_found(name: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": f"{name}不存在"})


def workflow_http_error(exc: WorkflowError) -> HTTPException:
    detail = {"code": exc.code, "message": exc.message}
    if exc.errors:
        detail["errors"] = exc.errors
    return HTTPException(status_code=exc.status_code, detail=detail)


def workflow_nodes_snapshot(db: Session, workflow: ScenarioWorkflowVersion) -> list[dict]:
    snapshots = []
    for node in workflow.nodes:
        config = dict(node.config or {})
        file_ids = list(config.get("contract_file_ids") or [])
        if file_ids:
            files = list(db.scalars(select(ContractDataFile).where(ContractDataFile.id.in_(file_ids))).all())
            by_id = {item.id: item for item in files}
            config["contract_files"] = [
                {
                    "id": item.id,
                    "filename": item.filename,
                    "contract_type": item.contract_type,
                    "quote_date": item.quote_date,
                    "row_count": item.row_count,
                    "size": item.size,
                    "checksum": item.checksum,
                    "remote_path": item.remote_path,
                }
                for file_id in file_ids
                if (item := by_id.get(file_id)) is not None
            ]
        snapshots.append({
            "id": node.id,
            "node_key": node.node_key,
            "position": node.position,
            "node_type": node.node_type,
            "name": node.name,
            "config": config,
        })
    return snapshots


def validate_scenario_resources(
    db: Session,
    plan: TestPlan,
    resource_ids: typing.List[int],
) -> typing.Tuple[typing.List[int], typing.List[str]]:
    if not resource_ids:
        raise HTTPException(status_code=400, detail={"code": "SCENARIO_RESOURCES_REQUIRED", "message": "场景至少需要选择一个资源"})
    if len(resource_ids) != len(set(resource_ids)):
        raise HTTPException(status_code=400, detail={"code": "DUPLICATE_RESOURCES", "message": "场景资源不能重复"})
    resources = list(db.scalars(select(Resource).where(Resource.id.in_(resource_ids), Resource.is_deleted.is_(False), Resource.is_enabled.is_(True))).all())
    if len(resources) != len(resource_ids):
        raise HTTPException(status_code=400, detail={"code": "INVALID_RESOURCES", "message": "资源不存在或已停用"})
    resources_by_id = {resource.id: resource for resource in resources}
    ordered = [resources_by_id[resource_id] for resource_id in resource_ids]
    if any(resource.business_code != plan.business_code for resource in ordered):
        raise HTTPException(status_code=400, detail={"code": "BUSINESS_MISMATCH", "message": "资源与方案业务不一致"})
    resource_types = [resource.resource_type for resource in ordered]
    if len(resource_types) != len(set(resource_types)):
        raise HTTPException(status_code=400, detail={"code": "DUPLICATE_RESOURCE_TYPES", "message": "每种资源类型最多选择一个资源"})
    return resource_ids, resource_types


def load_run(db: Session, run_id: int) -> TestRun:
    run = db.scalar(
        select(TestRun)
        .where(TestRun.id == run_id)
        .options(
            selectinload(TestRun.steps),
            selectinload(TestRun.metrics),
            selectinload(TestRun.artifacts),
            selectinload(TestRun.verdict),
            selectinload(TestRun.resource_links),
        )
    )
    if not run:
        raise not_found("运行")
    return run


def database_http_error(exc: DatabaseOperationError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


def database_resource(db: Session, resource_id: int, database_name: str) -> typing.Tuple[Resource, str]:
    resource = db.get(Resource, resource_id)
    if not resource or resource.is_deleted:
        raise not_found("资源")
    try:
        return resource, validate_database(resource, database_name)
    except DatabaseOperationError as exc:
        raise database_http_error(exc) from exc


def config_resource(db: Session, resource_id: int, resource_type: str) -> Resource:
    resource = db.get(Resource, resource_id)
    if not resource or resource.is_deleted:
        raise not_found("资源")
    if resource.resource_type != resource_type:
        code = "ORDER_RESOURCE_REQUIRED" if resource_type == "order" else "PARSER_RESOURCE_REQUIRED"
        raise HTTPException(status_code=400, detail={"code": code, "message": f"该资源不是 {resource_type} 类型"})
    return resource


def order_config_resource(db: Session, resource_id: int) -> Resource:
    return config_resource(db, resource_id, "order")


def parser_config_resource(db: Session, resource_id: int) -> Resource:
    return config_resource(db, resource_id, "parser")


def order_config_http_error(exc: OrderConfigError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})

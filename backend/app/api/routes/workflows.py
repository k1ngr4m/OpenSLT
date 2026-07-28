from __future__ import annotations

import typing

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.adapters.database import DatabaseOperationError
from app.api.deps import get_current_user, operators
from app.api.routes.common import database_http_error, not_found, validate_scenario_resources, workflow_http_error
from app.core.database import get_db
from app.models import ContractDataFile, Resource, ScenarioWorkflowNode, ScenarioWorkflowVersion, TestPlan, TestScenario, User
from app.schemas import CaptureSnapshotOut, ContractDataFetchRequest, ContractDataFileOut, WorkflowDocumentOut, WorkflowDocumentWrite, WorkflowVersionOut
from app.services.audit import write_audit
from app.services.resource_relations import node_contract_file_ids
from app.services.workflows import WorkflowError, clone_published_to_draft, fetch_contract_files, load_version, preview_node, publish, replace_draft, validate_structure, workflow_payload
from app.services.workflow_contracts import scan_remote_contract_files

router = APIRouter()

@router.post("/scenarios/{scenario_id}/workflow/draft", response_model=WorkflowDocumentOut)
def ensure_workflow_draft(scenario_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> dict:
    scenario = db.get(TestScenario, scenario_id)
    if not scenario or scenario.is_archived: raise not_found("场景")
    version = clone_published_to_draft(db, scenario, actor.id)
    write_audit(db, "workflow.draft", "test_scenario", scenario.id, actor, request, detail={"version_id": version.id}); db.commit()
    version = load_version(db, version.id)
    return workflow_payload(scenario, version, validate_structure(db, scenario, version))


@router.get("/scenarios/{scenario_id}/workflow", response_model=WorkflowDocumentOut)
def get_scenario_workflow(scenario_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    scenario = db.get(TestScenario, scenario_id)
    if not scenario or scenario.is_archived: raise not_found("场景")
    version_id = scenario.draft_workflow_version_id or scenario.published_workflow_version_id
    if not version_id: raise not_found("工作流")
    version = load_version(db, version_id)
    return workflow_payload(scenario, version, validate_structure(db, scenario, version))


@router.put("/scenarios/{scenario_id}/workflow", response_model=WorkflowDocumentOut)
def save_scenario_workflow(scenario_id: int, payload: WorkflowDocumentWrite, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> dict:
    scenario = db.get(TestScenario, scenario_id)
    if not scenario or scenario.is_archived: raise not_found("场景")
    if not scenario.draft_workflow_version_id:
        raise HTTPException(status_code=409, detail={"code": "WORKFLOW_DRAFT_REQUIRED", "message": "请先创建工作流草稿"})
    plan = db.get(TestPlan, scenario.plan_id)
    resource_ids, _ = validate_scenario_resources(db, plan, payload.resource_ids)
    version = load_version(db, scenario.draft_workflow_version_id)
    try:
        replace_draft(db, scenario, version, expected_revision=payload.expected_revision, resource_ids=resource_ids, nodes=[item.model_dump() for item in payload.nodes])
    except WorkflowError as exc:
        raise workflow_http_error(exc) from exc
    write_audit(db, "workflow.save", "test_scenario", scenario.id, actor, request, detail={"version_id": version.id, "revision": version.revision}); db.commit()
    version = load_version(db, version.id)
    return workflow_payload(scenario, version, validate_structure(db, scenario, version))


@router.get("/scenarios/{scenario_id}/workflow/versions", response_model=typing.List[WorkflowVersionOut])
def list_workflow_versions(scenario_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ScenarioWorkflowVersion]:
    if not db.get(TestScenario, scenario_id): raise not_found("场景")
    return list(db.scalars(select(ScenarioWorkflowVersion).where(ScenarioWorkflowVersion.scenario_id == scenario_id).options(selectinload(ScenarioWorkflowVersion.nodes)).order_by(ScenarioWorkflowVersion.version_no.desc())).all())


@router.post("/scenarios/{scenario_id}/workflow/nodes/{node_key}/preview", response_model=typing.List[CaptureSnapshotOut])
async def preview_workflow_node(scenario_id: int, node_key: str, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> list:
    scenario = db.get(TestScenario, scenario_id)
    if not scenario or not scenario.draft_workflow_version_id: raise not_found("工作流草稿")
    version = load_version(db, scenario.draft_workflow_version_id)
    node = next((item for item in version.nodes if item.node_key == node_key), None)
    if not node: raise not_found("节点")
    errors = [item for item in validate_structure(db, scenario, version) if item.get("node_key") == node_key]
    if errors: raise HTTPException(status_code=422, detail={"code": "NODE_VALIDATION_FAILED", "message": "节点配置未完成", "errors": errors})
    try:
        snapshots = await preview_node(db, scenario, version, node, actor.id)
    except (WorkflowError, DatabaseOperationError) as exc:
        if isinstance(exc, WorkflowError): raise workflow_http_error(exc) from exc
        raise database_http_error(exc) from exc
    write_audit(db, "workflow.node_preview", "workflow_node", node.id, actor, request, detail={"snapshot_ids": [item.id for item in snapshots]}); db.commit()
    return snapshots


@router.post("/scenarios/{scenario_id}/workflow/publish", response_model=WorkflowDocumentOut)
async def publish_scenario_workflow(scenario_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> dict:
    scenario = db.get(TestScenario, scenario_id)
    if not scenario or not scenario.draft_workflow_version_id: raise not_found("工作流草稿")
    version = load_version(db, scenario.draft_workflow_version_id)
    try:
        await publish(db, scenario, version, actor.id)
    except WorkflowError as exc:
        raise workflow_http_error(exc) from exc
    write_audit(db, "workflow.publish", "test_scenario", scenario.id, actor, request, detail={"version_id": version.id, "version_no": version.version_no}); db.commit()
    return workflow_payload(scenario, load_version(db, version.id), [])


@router.get("/scenarios/{scenario_id}/workflow/nodes/{node_key}/contract-files", response_model=typing.List[ContractDataFileOut])
def list_contract_files(scenario_id: int, node_key: str, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ContractDataFile]:
    node = db.scalar(select(ScenarioWorkflowNode).join(ScenarioWorkflowVersion).where(ScenarioWorkflowVersion.scenario_id == scenario_id, ScenarioWorkflowNode.node_key == node_key).order_by(ScenarioWorkflowVersion.version_no.desc()))
    if not node: raise not_found("节点")
    referenced_ids = node_contract_file_ids(node)
    criteria = [ContractDataFile.workflow_node_id == node.id]
    if referenced_ids:
        criteria.append(ContractDataFile.id.in_(referenced_ids))
    return list(db.scalars(
        select(ContractDataFile).where(or_(*criteria)).order_by(ContractDataFile.id.desc())
    ).all())


@router.post("/scenarios/{scenario_id}/workflow/nodes/{node_key}/contract-files/scan", response_model=typing.List[ContractDataFileOut])
async def scan_contract_files(scenario_id: int, node_key: str, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> list[ContractDataFile]:
    scenario = db.get(TestScenario, scenario_id)
    if not scenario or not scenario.draft_workflow_version_id: raise not_found("工作流草稿")
    version = load_version(db, scenario.draft_workflow_version_id)
    node = next((item for item in version.nodes if item.node_key == node_key), None)
    if not node: raise not_found("节点")
    try:
        await scan_remote_contract_files(db, scenario, version, node, actor.id)
    except WorkflowError as exc:
        raise workflow_http_error(exc) from exc
    write_audit(db, "workflow.contract_scan", "workflow_node", node.id, actor, request)
    db.commit()
    referenced_ids = node_contract_file_ids(node)
    criteria = [ContractDataFile.workflow_node_id == node.id]
    if referenced_ids:
        criteria.append(ContractDataFile.id.in_(referenced_ids))
    return list(db.scalars(
        select(ContractDataFile).where(or_(*criteria)).order_by(ContractDataFile.id.desc())
    ).all())


@router.post("/scenarios/{scenario_id}/workflow/nodes/{node_key}/contract-files/fetch", response_model=typing.List[ContractDataFileOut], status_code=201)
async def create_contract_files(scenario_id: int, node_key: str, payload: ContractDataFetchRequest, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> list[ContractDataFile]:
    scenario = db.get(TestScenario, scenario_id)
    if not scenario or not scenario.draft_workflow_version_id: raise not_found("工作流草稿")
    version = load_version(db, scenario.draft_workflow_version_id)
    node = next((item for item in version.nodes if item.node_key == node_key), None)
    if not node: raise not_found("节点")
    database_resource = db.get(Resource, payload.database_resource_id)
    if not database_resource or database_resource.is_deleted: raise not_found("数据库资源")
    try:
        files = await fetch_contract_files(db, scenario, version, node, database_resource, payload.database_name, payload.contract_types, actor.id)
    except WorkflowError as exc:
        raise workflow_http_error(exc) from exc
    write_audit(db, "workflow.contract_fetch", "workflow_node", node.id, actor, request, detail={"file_ids": [item.id for item in files]}); db.commit()
    return files

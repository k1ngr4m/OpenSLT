from __future__ import annotations

import typing

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, operators
from app.api.routes.common import not_found, validate_scenario_resources
from app.core.database import get_db
from app.models import ScenarioWorkflowVersion, TestPlan, TestRun, TestScenario, User
from app.schemas import PlanOut, PlanWrite, ScenarioOut, ScenarioWrite
from app.services.audit import write_audit
from app.services.workflows import copy_version_contents, create_draft, load_version
from app.services.resource_relations import (
    plan_resource_ids,
    scenario_resource_ids,
    sync_plan_resources,
    sync_scenario_resources,
    sync_workflow_resources,
    workflow_resource_ids,
)

router = APIRouter()

@router.get("/plans", response_model=typing.List[PlanOut])
def list_plans(business_code: typing.Union[str, None] = None, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> typing.List[TestPlan]:
    query = select(TestPlan)
    if business_code: query = query.where(TestPlan.business_code == business_code)
    return list(db.scalars(query.order_by(TestPlan.id.desc())).all())


@router.post("/plans", response_model=PlanOut, status_code=201)
def create_plan(payload: PlanWrite, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestPlan:
    values = payload.model_dump(exclude={"default_resource_ids"})
    plan = TestPlan(**values, created_by=actor.id)
    sync_plan_resources(plan, payload.default_resource_ids)
    db.add(plan); db.flush(); write_audit(db, "plan.create", "test_plan", plan.id, actor, request); db.commit(); return plan


@router.put("/plans/{plan_id}", response_model=PlanOut)
def update_plan(plan_id: int, payload: PlanWrite, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestPlan:
    plan = db.get(TestPlan, plan_id)
    if not plan: raise not_found("方案")
    for key, value in payload.model_dump(exclude={"default_resource_ids"}).items(): setattr(plan, key, value)
    sync_plan_resources(plan, payload.default_resource_ids, db)
    write_audit(db, "plan.update", "test_plan", plan.id, actor, request); db.commit(); return plan


@router.post("/plans/{plan_id}/copy", response_model=PlanOut, status_code=201)
def copy_plan(plan_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestPlan:
    original = db.scalar(select(TestPlan).where(TestPlan.id == plan_id).options(selectinload(TestPlan.scenarios)))
    if not original: raise not_found("方案")
    copied_resource_ids = plan_resource_ids(original)
    copied = TestPlan(name=f"{original.name} - 副本", business_code=original.business_code, description=original.description, config_version=original.config_version, created_by=actor.id)
    sync_plan_resources(copied, copied_resource_ids)
    db.add(copied); db.flush()
    for scenario in original.scenarios:
        if scenario.is_archived:
            continue
        source_version_id = scenario.published_workflow_version_id or scenario.draft_workflow_version_id
        source_version = load_version(db, source_version_id) if source_version_id else None
        resource_ids = workflow_resource_ids(source_version) if source_version else scenario_resource_ids(scenario)
        copied_scenario = TestScenario(plan_id=copied.id, name=scenario.name, scenario_type=scenario.scenario_type, config_version=scenario.config_version, expected_artifacts=scenario.expected_artifacts, default_resource_ids=resource_ids, required_resource_types=list(scenario.required_resource_types), is_enabled=False, workflow_status="draft")
        sync_scenario_resources(copied_scenario, resource_ids)
        db.add(copied_scenario); db.flush()
        draft = create_draft(db, copied_scenario, actor.id, resource_ids)
        if source_version: copy_version_contents(db, source_version, draft, actor.id)
    write_audit(db, "plan.copy", "test_plan", copied.id, actor, request, detail={"source_id": plan_id}); db.commit(); return copied


@router.delete("/plans/{plan_id}", status_code=204)
def delete_plan(plan_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> Response:
    plan = db.get(TestPlan, plan_id)
    if not plan: raise not_found("方案")
    if db.scalar(select(TestRun.id).where(TestRun.plan_id == plan_id).limit(1)): raise HTTPException(status_code=409, detail={"code": "PLAN_REFERENCED", "message": "方案已有运行历史，只能停用"})
    db.delete(plan); write_audit(db, "plan.delete", "test_plan", plan_id, actor, request); db.commit(); return Response(status_code=204)


@router.get("/scenarios", response_model=typing.List[ScenarioOut])
def list_scenarios(plan_id: typing.Union[int, None] = None, include_archived: bool = False, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> typing.List[TestScenario]:
    query = select(TestScenario)
    if not include_archived: query = query.where(TestScenario.is_archived.is_(False))
    if plan_id: query = query.where(TestScenario.plan_id == plan_id)
    return list(db.scalars(query.order_by(TestScenario.id.desc())).all())


@router.post("/scenarios", response_model=ScenarioOut, status_code=201)
def create_scenario(payload: ScenarioWrite, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestScenario:
    plan = db.get(TestPlan, payload.plan_id)
    if not plan: raise not_found("方案")
    values = payload.model_dump(
        exclude={"default_resource_ids", "is_enabled", "scenario_type", "config_version"}
    )
    if payload.default_resource_ids is not None:
        resource_ids, resource_types = validate_scenario_resources(db, plan, payload.default_resource_ids)
        values["default_resource_ids"] = resource_ids
        values["required_resource_types"] = resource_types
    else:
        values["default_resource_ids"] = []
    values["scenario_type"] = "order"
    values["config_version"] = "1.0"
    values["is_enabled"] = False
    values["workflow_status"] = "draft"
    scenario = TestScenario(**values)
    sync_scenario_resources(scenario, values["default_resource_ids"])
    db.add(scenario); db.flush()
    create_draft(db, scenario, actor.id, values["default_resource_ids"])
    write_audit(db, "scenario.create", "test_scenario", scenario.id, actor, request); db.commit(); return scenario


@router.put("/scenarios/{scenario_id}", response_model=ScenarioOut)
def update_scenario(scenario_id: int, payload: ScenarioWrite, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestScenario:
    scenario = db.get(TestScenario, scenario_id)
    if not scenario: raise not_found("场景")
    plan = db.get(TestPlan, payload.plan_id)
    if not plan: raise not_found("方案")
    values = payload.model_dump(
        exclude={"default_resource_ids", "is_enabled", "scenario_type", "config_version"}
    )
    if payload.default_resource_ids is not None:
        resource_ids, resource_types = validate_scenario_resources(db, plan, payload.default_resource_ids)
        values["default_resource_ids"] = resource_ids
        values["required_resource_types"] = resource_types
    elif scenario_resource_ids(scenario):
        resource_ids, resource_types = validate_scenario_resources(db, plan, scenario_resource_ids(scenario))
        values["default_resource_ids"] = resource_ids
        values["required_resource_types"] = resource_types
    for key, value in values.items(): setattr(scenario, key, value)
    if "default_resource_ids" in values:
        sync_scenario_resources(scenario, values["default_resource_ids"], db)
    if scenario.draft_workflow_version_id and "default_resource_ids" in values:
        draft = db.get(ScenarioWorkflowVersion, scenario.draft_workflow_version_id)
        if draft:
            sync_workflow_resources(draft, values["default_resource_ids"], db)
    write_audit(db, "scenario.update", "test_scenario", scenario.id, actor, request); db.commit(); return scenario


@router.post("/scenarios/{scenario_id}/copy", response_model=ScenarioOut, status_code=201)
def copy_scenario(scenario_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestScenario:
    source = db.get(TestScenario, scenario_id)
    if not source: raise not_found("场景")
    source_version_id = source.published_workflow_version_id or source.draft_workflow_version_id
    source_version = load_version(db, source_version_id) if source_version_id else None
    resource_ids = workflow_resource_ids(source_version) if source_version else scenario_resource_ids(source)
    copied = TestScenario(plan_id=source.plan_id, name=f"{source.name} - 副本", scenario_type=source.scenario_type, config_version=source.config_version, expected_artifacts=source.expected_artifacts, default_resource_ids=resource_ids, required_resource_types=list(source.required_resource_types), is_enabled=False, workflow_status="draft")
    sync_scenario_resources(copied, resource_ids)
    db.add(copied); db.flush()
    draft = create_draft(db, copied, actor.id, resource_ids)
    if source_version: copy_version_contents(db, source_version, draft, actor.id)
    write_audit(db, "scenario.copy", "test_scenario", copied.id, actor, request, detail={"source_id": scenario_id}); db.commit(); return copied



@router.delete("/scenarios/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> Response:
    scenario = db.get(TestScenario, scenario_id)
    if not scenario: raise not_found("场景")
    if db.scalar(select(TestRun.id).where(TestRun.scenario_id == scenario_id).limit(1)):
        raise HTTPException(status_code=409, detail={"code": "SCENARIO_REFERENCED", "message": "场景已有运行历史，只能停用"})
    db.delete(scenario); write_audit(db, "scenario.delete", "test_scenario", scenario_id, actor, request); db.commit(); return Response(status_code=204)

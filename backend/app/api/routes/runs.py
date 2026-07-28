from __future__ import annotations

import typing
from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, operators
from app.api.routes.common import load_run, not_found, workflow_http_error, workflow_nodes_snapshot
from app.core.database import get_db
from app.core.logging import trace_id_ctx
from app.core.time import beijing_now
from app.models import Artifact, ConfigurationCaptureSnapshot, LogRecord, Resource, TestPlan, TestRun, TestScenario, User, Verdict
from app.schemas import ArtifactOut, CaptureSnapshotOut, LogOut, RunCreate, RunOut, VerdictOut, VerdictWrite
from app.services.audit import write_audit
from app.services.durable_tasks import enqueue_task, schedule_task
from app.services.events import broker
from app.services.orchestration import begin_workflow_step, cancel_run, complete_workflow_step, create_workflow_steps, confirm_workflow_step, release_locks
from app.services.reports import generate_reports
from app.services.resource_relations import sync_run_resources
from app.services.run_state import PAUSABLE_RUN_STATUSES, TERMINAL_RUN_STATUSES, transition_run, transition_step
from app.services.workflows import WorkflowError, load_version, resource_map
from app.wiring_profiles import build_wiring_snapshot

router = APIRouter()

@router.post("/runs", response_model=RunOut, status_code=201)
def create_run(payload: RunCreate, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    plan = db.get(TestPlan, payload.plan_id); scenario = db.get(TestScenario, payload.scenario_id)
    if not plan or not plan.is_enabled: raise not_found("可用方案")
    if not scenario or not scenario.is_enabled or scenario.is_archived or scenario.plan_id != plan.id or not scenario.published_workflow_version_id: raise not_found("可用场景")
    workflow = load_version(db, scenario.published_workflow_version_id)
    resources = list(db.scalars(select(Resource).where(Resource.id.in_(payload.resource_ids), Resource.is_deleted.is_(False), Resource.is_enabled.is_(True))).all())
    if len(resources) != len(set(payload.resource_ids)): raise HTTPException(status_code=400, detail={"code": "INVALID_RESOURCES", "message": "资源不存在或已停用"})
    resources_by_id = {resource.id: resource for resource in resources}
    resources = [resources_by_id[resource_id] for resource_id in payload.resource_ids]
    if any(resource.business_code != plan.business_code for resource in resources): raise HTTPException(status_code=400, detail={"code": "BUSINESS_MISMATCH", "message": "资源与方案业务不一致"})
    provided_types = [resource.resource_type for resource in resources]
    if len(provided_types) != len(set(provided_types)):
        raise HTTPException(status_code=400, detail={"code": "DUPLICATE_RESOURCE_TYPES", "message": "每种资源类型只能选择一个资源"})
    required_types = set(resource_map(db, workflow))
    if set(provided_types) != required_types:
        missing = sorted(required_types - set(provided_types))
        extra = sorted(set(provided_types) - required_types)
        raise HTTPException(status_code=400, detail={"code": "RESOURCE_SET_MISMATCH", "message": f"运行资源类型与场景不一致，缺少: {missing}，多余: {extra}"})
    node_snapshots = workflow_nodes_snapshot(db, workflow)
    resource_snapshots = []
    for resource in resources:
        is_rem = resource.resource_type == "rem"
        resource_snapshots.append({
            "id": resource.id,
            "name": resource.name,
            "type": resource.resource_type,
            "business_code": resource.business_code,
            "host": resource.host,
            "version": resource.version_info,
            "trade_ip": resource.trade_ip if is_rem else None,
            "trade_tcp_port": resource.trade_tcp_port if is_rem else None,
            "trade_udp_port": resource.trade_udp_port if is_rem else None,
            "query_ip": resource.query_ip if is_rem else None,
            "query_port": resource.query_port if is_rem else None,
        })
    snapshot = {
        "plan": {
            "id": plan.id,
            "name": plan.name,
            "business_code": plan.business_code,
            "config_version": plan.config_version,
        },
        "scenario": {
            "id": scenario.id,
            "name": scenario.name,
            "scenario_type": scenario.scenario_type,
            "config_version": scenario.config_version,
        },
        "workflow": {
            "id": workflow.id,
            "version_no": workflow.version_no,
            "nodes": node_snapshots,
        },
        "resources": resource_snapshots,
    }
    timeout_at = (
        beijing_now() + timedelta(minutes=payload.timeout_minutes)
        if payload.timeout_minutes is not None
        else None
    )
    run = TestRun(run_number=f"R{beijing_now():%Y%m%d%H%M%S}-{uuid4().hex[:6].upper()}", plan_id=plan.id, scenario_id=scenario.id, workflow_version_id=workflow.id, business_code=plan.business_code, config_snapshot=snapshot, trace_id=trace_id_ctx.get() or str(uuid4()), created_by=actor.id, timeout_at=timeout_at)
    sync_run_resources(run, payload.resource_ids)
    create_workflow_steps(run, workflow)
    node_configs = {item["id"]: item["config"] for item in node_snapshots}
    resources_by_type = {resource.resource_type: resource for resource in resources}
    for step in run.steps:
        step.config_snapshot = dict(node_configs.get(step.workflow_node_id) or {})
        if step.node_type == "wiring_confirmation" and step.config_snapshot.get("diagram") == "resource":
            try:
                step.config_snapshot["wiring_snapshot"] = build_wiring_snapshot(
                    resources_by_type["rem"],
                    resources_by_type["market"],
                    resources_by_type["slnic"],
                    plan.business_code,
                )
            except (KeyError, ValueError) as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "WIRING_RESOURCE_INVALID",
                        "message": "REM、模拟市场或 SLNIC 接线配置不完整，请先更新资源",
                    },
                ) from exc
    db.add(run); db.flush(); write_audit(db, "run.create", "test_run", run.id, actor, request); db.commit(); return load_run(db, run.id)


@router.get("/runs", response_model=typing.List[RunOut])
def list_runs(business_code: typing.Union[str, None] = None, run_status: typing.Union[str, None] = Query(default=None, alias="status"), conclusion: typing.Union[str, None] = None, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> typing.List[TestRun]:
    query = select(TestRun).options(selectinload(TestRun.steps), selectinload(TestRun.metrics), selectinload(TestRun.artifacts), selectinload(TestRun.verdict), selectinload(TestRun.status_transitions))
    if business_code: query = query.where(TestRun.business_code == business_code)
    if run_status: query = query.where(TestRun.status == run_status)
    if conclusion: query = query.join(Verdict).where(Verdict.final_result == conclusion)
    return list(db.scalars(query.order_by(TestRun.id.desc()).limit(200)).unique().all())


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> TestRun:
    return load_run(db, run_id)


@router.get("/runs/{run_id}/steps/{step_id}/capture-snapshots", response_model=typing.List[CaptureSnapshotOut])
def list_run_step_capture_snapshots(run_id: int, step_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> typing.List[ConfigurationCaptureSnapshot]:
    run = load_run(db, run_id)
    if not any(step.id == step_id for step in run.steps):
        raise not_found("运行步骤")
    return list(db.scalars(
        select(ConfigurationCaptureSnapshot)
        .where(
            ConfigurationCaptureSnapshot.run_id == run_id,
            ConfigurationCaptureSnapshot.run_step_id == step_id,
        )
        .options(selectinload(ConfigurationCaptureSnapshot.items))
        .order_by(ConfigurationCaptureSnapshot.id)
    ).all())


@router.post("/runs/{run_id}/start", response_model=RunOut)
async def run_start(run_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    if run.status not in {"draft", "resource_queue"}: raise HTTPException(status_code=409, detail={"code": "INVALID_TRANSITION", "message": "当前状态不能启动"})
    transition_run(run, "resource_queue", source="api", actor_id=actor.id, reason="run started")
    task = enqueue_task(db, "start_run", {"run_id": run.id}, f"start-run:{run.id}:v{run.status_version}", reactivate=True)
    write_audit(db, "run.start", "test_run", run.id, actor, request); db.commit(); schedule_task(task.id); return run


@router.post("/runs/{run_id}/confirm-wiring", response_model=RunOut)
async def confirm_wiring(run_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    if run.status != "awaiting_wiring": raise HTTPException(status_code=409, detail={"code": "INVALID_TRANSITION", "message": "当前不等待接线确认"})
    task = enqueue_task(db, "continue_after_wiring", {"run_id": run.id}, f"continue-wiring:{run.id}:v{run.status_version}")
    write_audit(db, "run.wiring_confirm", "test_run", run.id, actor, request); db.commit(); schedule_task(task.id); return run


@router.post("/runs/{run_id}/steps/{step_id}/confirm", response_model=RunOut)
def confirm_run_step(run_id: int, step_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    try:
        confirm_workflow_step(db, run, step_id, actor.id)
    except WorkflowError as exc:
        raise workflow_http_error(exc) from exc
    write_audit(db, "run.step_confirm", "run_step", step_id, actor, request, detail={"run_id": run.id}); db.commit(); return load_run(db, run.id)


@router.post("/runs/{run_id}/steps/{step_id}/start", response_model=RunOut)
async def start_run_step(run_id: int, step_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    try:
        step = begin_workflow_step(db, run, step_id)
    except WorkflowError as exc:
        raise workflow_http_error(exc) from exc
    task = enqueue_task(db, "start_workflow_step", {"run_id": run.id, "step_id": step.id}, f"workflow-step:{run.id}:{step.id}:retry:{step.retry_count}")
    write_audit(db, "run.step_start", "run_step", step.id, actor, request, detail={"run_id": run.id}); db.commit(); schedule_task(task.id); return load_run(db, run.id)


@router.post("/runs/{run_id}/steps/{step_id}/complete", response_model=RunOut)
def complete_run_step(run_id: int, step_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    try:
        complete_workflow_step(db, run, step_id, actor.id)
    except WorkflowError as exc:
        raise workflow_http_error(exc) from exc
    write_audit(db, "run.step_complete", "run_step", step_id, actor, request, detail={"run_id": run.id}); db.commit(); return load_run(db, run.id)


@router.post("/runs/{run_id}/steps/{step_id}/retry", response_model=RunOut)
async def retry_run_step(run_id: int, step_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    try:
        step = begin_workflow_step(db, run, step_id, retry=True)
    except WorkflowError as exc:
        raise workflow_http_error(exc) from exc
    task = enqueue_task(db, "start_workflow_step", {"run_id": run.id, "step_id": step.id}, f"workflow-step:{run.id}:{step.id}:retry:{step.retry_count}")
    write_audit(db, "run.step_retry", "run_step", step.id, actor, request, detail={"run_id": run.id, "retry_count": step.retry_count}); db.commit(); schedule_task(task.id); return load_run(db, run.id)


@router.post("/runs/{run_id}/cancel", response_model=RunOut)
def run_cancel(run_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    if run.status in TERMINAL_RUN_STATUSES: raise HTTPException(status_code=409, detail={"code": "INVALID_TRANSITION", "message": "运行已结束"})
    cancel_run(db, run, actor_id=actor.id); write_audit(db, "run.cancel", "test_run", run.id, actor, request); db.commit(); return load_run(db, run.id)


@router.post("/runs/{run_id}/pause", response_model=RunOut)
def run_pause(run_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    if run.status not in PAUSABLE_RUN_STATUSES:
        raise HTTPException(status_code=409, detail={"code": "INVALID_TRANSITION", "message": "仅排队或人工节点可安全暂停"})
    run.paused_from = run.status; transition_run(run, "paused", source="api", actor_id=actor.id, reason="run paused")
    write_audit(db, "run.pause", "test_run", run.id, actor, request); db.commit(); return run


@router.post("/runs/{run_id}/resume", response_model=RunOut)
async def run_resume(run_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    if run.status != "paused" or not run.paused_from:
        raise HTTPException(status_code=409, detail={"code": "INVALID_TRANSITION", "message": "运行未暂停"})
    previous = run.paused_from; transition_run(run, previous, source="api", actor_id=actor.id, reason="run resumed"); run.paused_from = None
    task = None
    if previous == "resource_queue":
        task = enqueue_task(db, "start_run", {"run_id": run.id}, f"resume-run:{run.id}:v{run.status_version}")
    write_audit(db, "run.resume", "test_run", run.id, actor, request, detail={"resume_to": previous}); db.commit()
    if task is not None: schedule_task(task.id)
    return run


@router.post("/runs/{run_id}/retry", response_model=RunOut)
async def run_retry(run_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    if run.workflow_version_id:
        failed = next((step for step in run.steps if step.status == "failed"), None)
        if not failed:
            raise HTTPException(status_code=409, detail={"code": "INVALID_TRANSITION", "message": "当前没有可重试的节点"})
        try:
            begin_workflow_step(db, run, failed.id, retry=True)
        except WorkflowError as exc:
            raise workflow_http_error(exc) from exc
        task = enqueue_task(db, "start_workflow_step", {"run_id": run.id, "step_id": failed.id}, f"workflow-step:{run.id}:{failed.id}:retry:{failed.retry_count}")
        write_audit(db, "run.retry", "test_run", run.id, actor, request, detail={"step": failed.code}); db.commit(); schedule_task(task.id); return load_run(db, run.id)
    if run.status not in {"precheck_failed", "execution_failed", "parse_failed"}:
        raise HTTPException(status_code=409, detail={"code": "INVALID_TRANSITION", "message": "当前状态不能重试"})
    failed = next((step for step in run.steps if step.status == "failed"), None)
    if failed:
        transition_step(failed, "pending"); failed.error_message = None; failed.retry_count += 1
    transition_run(run, "resource_queue", source="api", actor_id=actor.id, reason="run retried"); run.error_code = None; run.error_message = None; run.finished_at = None
    task = enqueue_task(db, "start_run", {"run_id": run.id}, f"retry-run:{run.id}:v{run.status_version}")
    write_audit(db, "run.retry", "test_run", run.id, actor, request, detail={"step": failed.code if failed else None}); db.commit(); schedule_task(task.id); return run


@router.post("/runs/{run_id}/verdict", response_model=VerdictOut)
def submit_verdict(run_id: int, payload: VerdictWrite, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> Verdict:
    run = load_run(db, run_id)
    if run.status not in {"awaiting_review", "completed"}: raise HTTPException(status_code=409, detail={"code": "INVALID_TRANSITION", "message": "当前状态不能提交结论"})
    verdict = run.verdict or Verdict(run_id=run.id)
    verdict.final_result = payload.final_result; verdict.issue_description = payload.issue_description; verdict.notes = payload.notes; verdict.reviewed_by = actor.id; verdict.reviewed_at = beijing_now()
    if not run.verdict: db.add(verdict)
    review = next(step for step in run.steps if step.code == "manual_review"); transition_step(review, "succeeded"); review.progress = 100; review.started_at = review.started_at or verdict.reviewed_at; review.finished_at = verdict.reviewed_at; review.duration_ms = 0
    report_step = next(step for step in run.steps if step.code == "reporting"); transition_step(report_step, "running"); report_step.started_at = beijing_now()
    db.flush(); generate_reports(db, run)
    transition_step(report_step, "succeeded"); report_step.progress = 100; report_step.finished_at = beijing_now(); report_step.duration_ms = int((report_step.finished_at - report_step.started_at).total_seconds() * 1000)
    transition_run(run, "completed", source="api", actor_id=actor.id, reason="verdict submitted"); run.progress = 100; run.finished_at = beijing_now(); release_locks(db, run.id, "completed")
    write_audit(db, "run.verdict_submit", "test_run", run.id, actor, request, detail={"final_result": payload.final_result}); db.commit(); broker.publish(run.id, {"type": "status", "status": "completed", "progress": 100}); return verdict


@router.post("/runs/{run_id}/reports", response_model=typing.List[ArtifactOut])
def regenerate_reports(run_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> typing.List[Artifact]:
    run = load_run(db, run_id)
    if run.status != "completed": raise HTTPException(status_code=409, detail={"code": "RUN_NOT_COMPLETE", "message": "运行完成后才能生成报告"})
    artifacts = generate_reports(db, run); write_audit(db, "report.regenerate", "test_run", run.id, actor, request); db.commit(); return artifacts


@router.get("/runs/{run_id}/logs", response_model=typing.List[LogOut])
def list_run_logs(run_id: int, level: typing.Union[str, None] = None, source: typing.Union[str, None] = None, keyword: typing.Union[str, None] = None, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> typing.List[LogRecord]:
    query = select(LogRecord).where(LogRecord.run_id == run_id)
    if level: query = query.where(LogRecord.level == level.upper())
    if source: query = query.where(LogRecord.source == source)
    if keyword: query = query.where(LogRecord.message.contains(keyword))
    return list(db.scalars(query.order_by(LogRecord.created_at).limit(5000)).all())

from __future__ import annotations

import shutil
import typing
from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, operators
from app.api.routes.common import load_run, not_found, workflow_http_error, workflow_nodes_snapshot
from app.core.config import settings
from app.core.database import get_db
from app.core.logging import logger, trace_id_ctx
from app.core.time import beijing_now
from app.models import (
    Artifact,
    ConfigurationCaptureItem,
    ConfigurationCaptureSnapshot,
    DurableTask,
    LogRecord,
    Metric,
    Resource,
    ResourceLock,
    RunResource,
    RunStatusTransition,
    RunStep,
    TestPlan,
    TestRun,
    TestScenario,
    User,
    Verdict,
)
from app.adapters.database import DatabaseOperationError, validate_database
from app.schemas import ArtifactOut, CaptureSnapshotOut, LogOut, OrderActionRequest, ParserTableExportOut, ParserTableExportRequest, RunCreate, RunOut, StatisticsCsvFilesOut, StatisticsInputSelectionOut, StatisticsInputSelectionRequest, VerdictOut, VerdictWrite, WiringInterfaceNamesWrite
from app.services.audit import write_audit
from app.services.durable_tasks import enqueue_task, schedule_task
from app.services.events import broker
from app.services.orchestration import append_log, begin_workflow_step, cancel_run, complete_workflow_step, create_workflow_steps, confirm_workflow_step, release_locks
from app.services.order_sessions import cleanup_order_session, order_session_name, send_order_action, supported_order_actions
from app.services.resource_relations import run_resource_ids, sync_run_resources
from app.services.reports import generate_reports
from app.services.run_state import PAUSABLE_RUN_STATUSES, TERMINAL_RUN_STATUSES, transition_run, transition_step
from app.services.statistics_execution import list_statistics_csv_files, select_statistics_inputs
from app.services.parser_inputs import PARSER_TABLES
from app.services.workflows import WorkflowError, collect_parser_outputs, export_parser_table_snapshot, load_version, resolve_parser_table_database, resource_map
from app.wiring_profiles import build_wiring_snapshot

router = APIRouter()
ORDER_ACTION_HISTORY_LIMIT = 100
DELETABLE_RUN_STATUSES = TERMINAL_RUN_STATUSES | frozenset(
    {
        "draft",
        "awaiting_wiring",
        "awaiting_review",
        "awaiting_step_start",
        "awaiting_step_completion",
        "awaiting_step_retry",
        "paused",
    }
)

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
                    client_interface_name=step.config_snapshot.get("client_interface_name"),
                    market_interface_name=step.config_snapshot.get("market_interface_name"),
                    auxiliary_interface_names=step.config_snapshot.get("auxiliary_interface_names"),
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


async def _cleanup_run_order_sessions(db: Session, run: TestRun) -> typing.List[str]:
    failures = []
    for step in run.steps:
        summary = dict(step.result_summary or {})
        if step.node_type != "order_preparation" or not summary.get("process_started"):
            continue
        if summary.get("session_status") == "closed":
            continue
        try:
            _step, resource = _order_step_resource(db, run, step.id)
            session = str(summary.get("tmux_session") or order_session_name(run.id, step.id))
            await cleanup_order_session(resource, session)
            step.result_summary = {**summary, "session_status": "closed"}
        except WorkflowError as exc:
            step.result_summary = {
                **summary,
                "session_status": "cleanup_failed",
                "session_error": exc.message,
            }
            failures.append(step.name)
    return failures


def _delete_run_database_records(db: Session, run_id: int) -> None:
    snapshot_ids = list(
        db.scalars(
            select(ConfigurationCaptureSnapshot.id).where(
                ConfigurationCaptureSnapshot.run_id == run_id
            )
        ).all()
    )
    if snapshot_ids:
        db.execute(
            delete(ConfigurationCaptureItem).where(
                ConfigurationCaptureItem.snapshot_id.in_(snapshot_ids)
            )
        )
    for model in (
        ConfigurationCaptureSnapshot,
        LogRecord,
        Artifact,
        Metric,
        Verdict,
        ResourceLock,
        RunResource,
        RunStatusTransition,
    ):
        db.execute(delete(model).where(model.run_id == run_id))
    db.execute(delete(RunStep).where(RunStep.run_id == run_id))

    db.execute(delete(DurableTask).where(DurableTask.run_id == run_id))
    db.execute(delete(TestRun).where(TestRun.id == run_id))


@router.delete("/runs/{run_id}", status_code=204)
async def delete_run(
    run_id: int,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> Response:
    run = load_run(db, run_id)
    if run.status not in DELETABLE_RUN_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "RUN_DELETE_NOT_ALLOWED",
                "message": "运行正在自动执行或排队，请先取消后再删除",
            },
        )

    original_status = run.status
    if run.status not in TERMINAL_RUN_STATUSES:
        cleanup_failures = await _cleanup_run_order_sessions(db, run)
        if cleanup_failures:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "RUN_CLEANUP_FAILED",
                    "message": "远端运行环境清理失败，请处理后重试删除",
                },
            )
        cancel_run(db, run, reason="run_deleted", actor_id=actor.id)

    run_number = run.run_number
    artifact_root = settings.artifact_root.resolve()
    artifact_directory = (
        artifact_root
        / run.business_code
        / str(run.plan_id)
        / str(run.scenario_id)
        / run.run_number
    ).resolve()
    if artifact_root not in artifact_directory.parents:
        artifact_directory = None

    _delete_run_database_records(db, run.id)
    write_audit(
        db,
        "run.delete",
        "test_run",
        run.id,
        actor,
        request,
        detail={"run_number": run_number, "status": original_status},
    )
    db.commit()

    if artifact_directory is not None and artifact_directory.is_dir():
        try:
            shutil.rmtree(artifact_directory)
        except OSError:
            logger.exception(
                "run_artifact_cleanup_failed",
                run_id=run_id,
                artifact_directory=str(artifact_directory),
            )
    broker.publish(run_id, {"type": "deleted", "run_id": run_id})
    return Response(status_code=204)


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


@router.put(
    "/runs/{run_id}/steps/{step_id}/wiring-interface-names",
    response_model=RunOut,
)
def update_wiring_interface_names(
    run_id: int,
    step_id: int,
    payload: WiringInterfaceNamesWrite,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> TestRun:
    db.scalar(
        select(RunStep)
        .where(RunStep.id == step_id, RunStep.run_id == run_id)
        .with_for_update()
    )
    run = load_run(db, run_id)
    step = next((item for item in run.steps if item.id == step_id), None)
    try:
        if not step or step.node_type != "wiring_confirmation":
            raise WorkflowError("WIRING_NODE_REQUIRED", "当前节点不是接线确认节点", 409)
        current = next((item for item in run.steps if item.status != "succeeded"), None)
        allowed = (
            (run.status == "awaiting_step_start" and step.status == "pending")
            or (run.status == "awaiting_step_completion" and step.status == "waiting")
        )
        if not allowed or not current or current.id != step.id:
            raise WorkflowError("WIRING_EDIT_NOT_ALLOWED", "当前接线确认节点不能修改网卡名称", 409)

        config = dict(step.config_snapshot or {})
        snapshot_value = config.get("wiring_snapshot")
        if not isinstance(snapshot_value, dict):
            raise WorkflowError("WIRING_SNAPSHOT_REQUIRED", "当前节点没有可编辑的接线图", 409)
        snapshot = dict(snapshot_value)
        auxiliary_names = list(payload.auxiliary_interface_names)
        is_soft_core = snapshot.get("topology_kind") == "soft_core"
        if is_soft_core and auxiliary_names:
            raise WorkflowError("WIRING_INTERFACE_COUNT_INVALID", "软核接线图只允许配置两个接口名称", 422)
        if not is_soft_core and len(auxiliary_names) != 2:
            raise WorkflowError("WIRING_INTERFACE_COUNT_INVALID", "整合版接线图需要配置四个接口名称", 422)

        old_names = {
            "client_interface_name": str((snapshot.get("client_interface") or {}).get("name") or ""),
            "market_interface_name": str((snapshot.get("market_interface") or {}).get("name") or ""),
            "auxiliary_interface_names": list(snapshot.get("auxiliary_interfaces") or []),
        }
        new_names = {
            "client_interface_name": payload.client_interface_name,
            "market_interface_name": payload.market_interface_name,
            "auxiliary_interface_names": auxiliary_names,
        }
        snapshot["client_interface"] = {
            **dict(snapshot.get("client_interface") or {}),
            "name": payload.client_interface_name,
        }
        snapshot["market_interface"] = {
            **dict(snapshot.get("market_interface") or {}),
            "name": payload.market_interface_name,
        }
        snapshot["auxiliary_interfaces"] = auxiliary_names
        config.update(new_names)
        config["wiring_snapshot"] = snapshot
        step.config_snapshot = config
        detail = {"run_id": run.id, "old_names": old_names, "new_names": new_names}
        append_log(
            db,
            run,
            "wiring.interface_names_updated",
            "接线网卡名称已更新",
            step=step,
            source="user",
            detail=detail,
        )
        write_audit(
            db,
            "run.wiring_interface_names",
            "run_step",
            step.id,
            actor,
            request,
            detail=detail,
        )
        db.commit()
        return load_run(db, run.id)
    except WorkflowError as exc:
        write_audit(
            db,
            "run.wiring_interface_names",
            "run_step",
            step_id,
            actor,
            request,
            result="failed",
            detail={"run_id": run.id, "code": exc.code},
        )
        db.commit()
        raise workflow_http_error(exc) from exc


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
async def confirm_run_step(run_id: int, step_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    try:
        await confirm_workflow_step(db, run, step_id, actor.id)
    except WorkflowError as exc:
        raise workflow_http_error(exc) from exc
    write_audit(db, "run.step_confirm", "run_step", step_id, actor, request, detail={"run_id": run.id}); db.commit(); return load_run(db, run.id)


@router.post("/runs/{run_id}/steps/{step_id}/start", response_model=RunOut)
async def start_run_step(run_id: int, step_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    db.scalar(select(RunStep).where(RunStep.id == step_id).with_for_update())
    run = load_run(db, run_id)
    try:
        current = next((item for item in run.steps if item.id == step_id), None)
        if current and current.node_type == "parser_parse":
            raise WorkflowError("PARSER_TERMINAL_REQUIRED", "数据解析节点必须通过 SSH 终端启动", 409)
        step = begin_workflow_step(db, run, step_id)
    except WorkflowError as exc:
        raise workflow_http_error(exc) from exc
    task = enqueue_task(db, "start_workflow_step", {"run_id": run.id, "step_id": step.id}, f"workflow-step:{run.id}:{step.id}:retry:{step.retry_count}")
    write_audit(db, "run.step_start", "run_step", step.id, actor, request, detail={"run_id": run.id}); db.commit(); schedule_task(task.id); return load_run(db, run.id)


@router.post("/runs/{run_id}/steps/{step_id}/parser-exports", response_model=ParserTableExportOut)
async def export_run_parser_table(
    run_id: int,
    step_id: int,
    payload: ParserTableExportRequest,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict[str, typing.Any]:
    db.scalar(select(RunStep).where(RunStep.id == step_id).with_for_update())
    run = load_run(db, run_id)
    step = next((item for item in run.steps if item.id == step_id), None)
    try:
        if not step or step.node_type != "parser_parse":
            raise WorkflowError("PARSER_NODE_REQUIRED", "当前节点不是数据解析节点", 409)
        current = next((item for item in run.steps if item.status != "succeeded"), None)
        allowed = (
            (run.status == "awaiting_step_start" and step.status == "pending")
            or (run.status == "awaiting_step_retry" and step.status == "failed")
        )
        if not allowed or not current or current.id != step.id:
            raise WorkflowError("PARSER_EXPORT_NOT_ALLOWED", "当前数据解析节点不能获取 CSV", 409)
        if payload.table not in PARSER_TABLES:
            raise WorkflowError("PARSER_TABLE_INVALID", f"不支持导出数据表 {payload.table}", 400)
        database_resource = db.scalar(select(Resource).where(
            Resource.id.in_(run_resource_ids(run)),
            Resource.resource_type == "database",
        ))
        if not database_resource:
            raise WorkflowError("PARSER_DATABASE_REQUIRED", "运行资源缺少数据库", 409)
        database_name = str((step.config_snapshot or {}).get("database_name") or "").strip()
        try:
            database_name = validate_database(database_resource, database_name)
        except DatabaseOperationError as exc:
            raise WorkflowError(exc.code, exc.message, exc.status_code) from exc
        table_database_name = resolve_parser_table_database(
            database_resource,
            database_name,
            payload.table,
        )
        result = await export_parser_table_snapshot(
            db,
            run,
            step,
            database_resource,
            table_database_name,
            payload.table,
            source="manual",
            actor_id=actor.id,
        )
        detail = {key: result[key] for key in ("table", "artifact_id", "row_count", "checksum", "source")}
        empty = int(result.get("row_count") or 0) == 0
        append_log(
            db,
            run,
            "parser.table_skipped" if empty else "parser.table_exported",
            f"{payload.table} 没有记录，已跳过" if empty else f"已获取 {payload.table}.csv",
            step=step,
            source="user",
            detail=detail,
        )
        write_audit(db, "run.parser_table_export", "run_step", step.id, actor, request, detail={"run_id": run.id, **detail})
        db.commit()
        return result
    except WorkflowError as exc:
        write_audit(
            db,
            "run.parser_table_export",
            "run_step",
            step_id,
            actor,
            request,
            result="failed",
            detail={"run_id": run.id, "table": payload.table, "code": exc.code},
        )
        db.commit()
        raise workflow_http_error(exc) from exc


@router.put(
    "/runs/{run_id}/steps/{step_id}/statistics-inputs",
    response_model=StatisticsInputSelectionOut,
)
async def update_statistics_inputs(
    run_id: int,
    step_id: int,
    payload: StatisticsInputSelectionRequest,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict[str, typing.Any]:
    db.scalar(select(RunStep).where(RunStep.id == step_id).with_for_update())
    run = load_run(db, run_id)
    step = next((item for item in run.steps if item.id == step_id), None)
    try:
        if not step or step.node_type != "data_statistics":
            raise WorkflowError("STATISTICS_NODE_REQUIRED", "当前节点不是数据统计节点", 409)
        current = next((item for item in run.steps if item.status != "succeeded"), None)
        allowed = (
            (run.status == "awaiting_step_start" and step.status == "pending")
            or (run.status == "awaiting_step_retry" and step.status == "failed")
        )
        if not allowed or not current or current.id != step.id:
            raise WorkflowError("STATISTICS_SELECTION_NOT_ALLOWED", "当前数据统计节点不能选择输入", 409)
        parser_resource = db.scalar(select(Resource).where(
            Resource.id.in_(run_resource_ids(run)),
            Resource.resource_type == "parser",
        ))
        if not parser_resource:
            raise WorkflowError("PARSER_RESOURCE_REQUIRED", "运行资源缺少解析工具", 409)
        result = await select_statistics_inputs(
            db, run, step, parser_resource, payload.relative_paths, actor.id
        )
        detail = {
            "run_id": run.id,
            "relative_paths": [item["relative_path"] for item in result["inputs"]],
        }
        append_log(
            db,
            run,
            "statistics.inputs_selected",
            f"已选择 {len(result['inputs'])} 个统计输入",
            step=step,
            source="user",
            detail=detail,
        )
        write_audit(
            db, "run.statistics_inputs", "run_step", step.id, actor, request, detail=detail
        )
        db.commit()
        return result
    except WorkflowError as exc:
        write_audit(
            db,
            "run.statistics_inputs",
            "run_step",
            step_id,
            actor,
            request,
            result="failed",
            detail={"run_id": run.id, "relative_paths": payload.relative_paths, "code": exc.code},
        )
        db.commit()
        raise workflow_http_error(exc) from exc


@router.get(
    "/runs/{run_id}/steps/{step_id}/statistics-csv-files",
    response_model=StatisticsCsvFilesOut,
)
async def get_statistics_csv_files(
    run_id: int,
    step_id: int,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict[str, typing.Any]:
    _ = actor
    run = load_run(db, run_id)
    step = next((item for item in run.steps if item.id == step_id), None)
    try:
        if not step or step.node_type != "data_statistics":
            raise WorkflowError("STATISTICS_NODE_REQUIRED", "当前节点不是数据统计节点", 409)
        current = next((item for item in run.steps if item.status != "succeeded"), None)
        allowed = (
            (run.status == "awaiting_step_start" and step.status == "pending")
            or (run.status == "awaiting_step_retry" and step.status == "failed")
        )
        if not allowed or not current or current.id != step.id:
            raise WorkflowError("STATISTICS_SELECTION_NOT_ALLOWED", "当前数据统计节点不能选择输入", 409)
        parser_resource = db.scalar(select(Resource).where(
            Resource.id.in_(run_resource_ids(run)),
            Resource.resource_type == "parser",
        ))
        if not parser_resource:
            raise WorkflowError("PARSER_RESOURCE_REQUIRED", "运行资源缺少解析工具", 409)
        return await list_statistics_csv_files(parser_resource, run, step)
    except WorkflowError as exc:
        raise workflow_http_error(exc) from exc


def _order_step_resource(db: Session, run: TestRun, step_id: int) -> typing.Tuple[typing.Any, Resource]:
    step = next((item for item in run.steps if item.id == step_id), None)
    if not step or step.node_type != "order_preparation":
        raise WorkflowError("ORDER_NODE_REQUIRED", "当前节点不是发单节点", 409)
    resource = db.scalar(select(Resource).where(
        Resource.id.in_(run_resource_ids(run)),
        Resource.resource_type == "order",
    ))
    if not resource:
        raise WorkflowError("ORDER_RESOURCE_REQUIRED", "运行资源缺少发单工具", 409)
    return step, resource


def _order_action_history(summary: typing.Mapping[str, typing.Any]) -> typing.List[typing.Dict[str, typing.Any]]:
    raw = summary.get("order_action_history")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)][-ORDER_ACTION_HISTORY_LIMIT:]


def _update_order_action_history(
    summary: typing.Dict[str, typing.Any],
    request_id: str,
    **changes: typing.Any,
) -> typing.Dict[str, typing.Any]:
    history = _order_action_history(summary)
    for item in reversed(history):
        if item.get("request_id") == request_id:
            item.update(changes)
            break
    summary["order_action_history"] = history[-ORDER_ACTION_HISTORY_LIMIT:]
    return summary


@router.post("/runs/{run_id}/steps/{step_id}/order-action", response_model=RunOut)
async def dispatch_order_action(
    run_id: int,
    step_id: int,
    request: Request,
    payload: typing.Union[OrderActionRequest, None] = None,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> TestRun:
    db.scalar(select(RunStep).where(RunStep.id == step_id).with_for_update())
    run = load_run(db, run_id)
    try:
        step, resource = _order_step_resource(db, run, step_id)
        if run.status != "awaiting_step_completion" or step.status != "waiting":
            raise WorkflowError("INVALID_TRANSITION", "当前发单节点不能发送动作", 409)
        summary = dict(step.result_summary or {})
        status = str(summary.get("order_action_status") or "pending")
        if status in {"dispatching", "unknown"}:
            raise WorkflowError("ORDER_ACTION_UNRESOLVED", "上一条发单动作结果尚未确认，不能继续发送", 409)
        action = str(payload.action if payload else (step.config_snapshot or {}).get("order_action") or "new_order")
        if action not in supported_order_actions(resource):
            raise WorkflowError("ORDER_ACTION_UNSUPPORTED", "发单资源不支持动作 %s" % action, 409)
        session = str(summary.get("tmux_session") or order_session_name(run.id, step.id))
        request_id = str(uuid4())
        dispatch_started_at = beijing_now()
        history = _order_action_history(summary)
        history.append({
            "request_id": request_id,
            "action": action,
            "status": "dispatching",
            "requested_by": actor.id,
            "started_at": dispatch_started_at.isoformat(),
            "finished_at": None,
            "error": None,
        })
        summary.update({
            "order_action": action,
            "order_action_status": "dispatching",
            "action_dispatched_by": actor.id,
            "action_dispatch_started_at": dispatch_started_at.isoformat(),
            "order_action_history": history[-ORDER_ACTION_HISTORY_LIMIT:],
        })
        step.result_summary = summary
        db.commit()
        try:
            await send_order_action(resource, session, action)
        except WorkflowError as exc:
            run = load_run(db, run_id)
            step = next(item for item in run.steps if item.id == step_id)
            failed_at = beijing_now()
            failed_summary = {
                **(step.result_summary or {}),
                "order_action_status": "unknown",
                "order_action_error": exc.message,
            }
            step.result_summary = _update_order_action_history(
                failed_summary,
                request_id,
                status="unknown",
                finished_at=failed_at.isoformat(),
                error=exc.message,
            )
            detail = {"action": action, "request_id": request_id, "error": exc.message}
            append_log(db, run, "order.action_unknown", "发单动作发送结果不确定，请查看终端后确认", level="WARNING", step=step, source="terminal", detail=detail)
            write_audit(db, "run.order_action", "run_step", step.id, actor, request, result="unknown", detail={"run_id": run.id, **detail})
            db.commit()
            raise
        run = load_run(db, run_id)
        step = next(item for item in run.steps if item.id == step_id)
        dispatched_at = beijing_now()
        succeeded_summary = {
            **(step.result_summary or {}),
            "order_action_status": "dispatched",
            "action_dispatched_at": dispatched_at.isoformat(),
            "order_action_error": None,
        }
        step.result_summary = _update_order_action_history(
            succeeded_summary,
            request_id,
            status="dispatched",
            finished_at=dispatched_at.isoformat(),
            error=None,
        )
        detail = {"action": action, "request_id": request_id, "tmux_session": session}
        append_log(db, run, "order.action_dispatched", "已发送发单动作 %s" % action, step=step, source="terminal", detail=detail)
        write_audit(db, "run.order_action", "run_step", step.id, actor, request, detail={"run_id": run.id, **detail})
        db.commit()
        broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})
        return load_run(db, run.id)
    except WorkflowError as exc:
        raise workflow_http_error(exc) from exc


@router.post("/runs/{run_id}/steps/{step_id}/order-action/confirm", response_model=RunOut)
def confirm_order_action(run_id: int, step_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    try:
        step, _resource = _order_step_resource(db, run, step_id)
        if (step.result_summary or {}).get("order_action_status") not in {"unknown", "dispatching"}:
            raise WorkflowError("ORDER_ACTION_CONFIRM_INVALID", "只有发送结果不确定的动作可以人工确认", 409)
        now = beijing_now()
        summary = {
            **(step.result_summary or {}),
            "order_action_status": "dispatched",
            "action_confirmed_by": actor.id,
            "action_confirmed_at": now.isoformat(),
            "order_action_error": None,
        }
        history = _order_action_history(summary)
        pending = next((item for item in reversed(history) if item.get("status") in {"unknown", "dispatching"}), None)
        if pending:
            pending.update({
                "status": "dispatched",
                "finished_at": pending.get("finished_at") or now.isoformat(),
                "error": None,
                "confirmed_by": actor.id,
                "confirmed_at": now.isoformat(),
            })
        summary["order_action_history"] = history
        step.result_summary = summary
        append_log(db, run, "order.action_confirmed", "操作员确认发单动作已发送", step=step, source="user")
        write_audit(db, "run.order_action_confirm", "run_step", step.id, actor, request, detail={"run_id": run.id})
        db.commit()
        return load_run(db, run.id)
    except WorkflowError as exc:
        raise workflow_http_error(exc) from exc


@router.post("/runs/{run_id}/steps/{step_id}/complete", response_model=RunOut)
async def complete_run_step(run_id: int, step_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    try:
        step = next((item for item in run.steps if item.id == step_id), None)
        if step and step.node_type == "parser_parse":
            current = next((item for item in run.steps if item.status != "succeeded"), None)
            if run.status != "awaiting_step_completion" or step.status != "waiting" or not current or current.id != step.id:
                raise WorkflowError("INVALID_WORKFLOW_STEP", "只能完成当前已执行的解析节点", 409)
            parser_resource = db.scalar(select(Resource).where(
                Resource.id.in_(run_resource_ids(run)),
                Resource.resource_type == "parser",
                Resource.is_deleted.is_(False),
                Resource.is_enabled.is_(True),
            ))
            if not parser_resource:
                raise WorkflowError("PARSER_RESOURCE_REQUIRED", "运行资源缺少已启用的解析工具", 409)
            step.result_summary = await collect_parser_outputs(db, run, step, parser_resource)
        if step and step.node_type == "order_preparation":
            current = next((item for item in run.steps if item.status != "succeeded"), None)
            if run.status != "awaiting_step_completion" or step.status != "waiting" or not current or current.id != step.id:
                raise WorkflowError("INVALID_WORKFLOW_STEP", "只能完成当前已执行的节点", 409)
            if (step.result_summary or {}).get("order_action_status") in {"dispatching", "unknown"}:
                raise WorkflowError("ORDER_ACTION_UNRESOLVED", "发单动作结果尚未确认，不能完成节点", 409)
            _step, resource = _order_step_resource(db, run, step_id)
            session = str((step.result_summary or {}).get("tmux_session") or order_session_name(run.id, step.id))
            await cleanup_order_session(resource, session)
            step.result_summary = {**(step.result_summary or {}), "session_status": "closed"}
        await complete_workflow_step(db, run, step_id, actor.id)
    except WorkflowError as exc:
        raise workflow_http_error(exc) from exc
    write_audit(db, "run.step_complete", "run_step", step_id, actor, request, detail={"run_id": run.id}); db.commit(); return load_run(db, run.id)


@router.post("/runs/{run_id}/steps/{step_id}/retry", response_model=RunOut)
async def retry_run_step(run_id: int, step_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    db.scalar(select(RunStep).where(RunStep.id == step_id).with_for_update())
    run = load_run(db, run_id)
    try:
        current = next((item for item in run.steps if item.id == step_id), None)
        if current and current.node_type == "parser_parse":
            raise WorkflowError("PARSER_TERMINAL_REQUIRED", "数据解析节点必须通过 SSH 终端重试", 409)
        if (
            current
            and current.node_type == "order_preparation"
            and current.status == "waiting"
            and run.status == "awaiting_step_completion"
            and (current.result_summary or {}).get("order_action_status") in {"unknown", "dispatching"}
        ):
            _step, resource = _order_step_resource(db, run, step_id)
            session = str((current.result_summary or {}).get("tmux_session") or order_session_name(run.id, current.id))
            await cleanup_order_session(resource, session)
            current.result_summary = {**(current.result_summary or {}), "session_status": "closed"}
            transition_step(current, "failed")
            current.error_message = "发单动作结果不确定，操作员选择重试节点"
            transition_run(run, "awaiting_step_retry")
        step = begin_workflow_step(db, run, step_id, retry=True)
    except WorkflowError as exc:
        raise workflow_http_error(exc) from exc
    task = enqueue_task(db, "start_workflow_step", {"run_id": run.id, "step_id": step.id}, f"workflow-step:{run.id}:{step.id}:retry:{step.retry_count}")
    write_audit(db, "run.step_retry", "run_step", step.id, actor, request, detail={"run_id": run.id, "retry_count": step.retry_count}); db.commit(); schedule_task(task.id); return load_run(db, run.id)


@router.post("/runs/{run_id}/cancel", response_model=RunOut)
async def run_cancel(run_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    if run.status in TERMINAL_RUN_STATUSES: raise HTTPException(status_code=409, detail={"code": "INVALID_TRANSITION", "message": "运行已结束"})
    await _cleanup_run_order_sessions(db, run)
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
        if failed.node_type == "parser_parse":
            raise HTTPException(status_code=409, detail={"code": "PARSER_TERMINAL_REQUIRED", "message": "数据解析节点必须通过 SSH 终端重试"})
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
    if not run.verdict:
        run.verdict = verdict
    db.flush()
    if run.workflow_version_id:
        report_step = next((step for step in run.steps if step.node_type == "report_generation"), None)
        report_result = generate_reports(db, run, step=report_step, reason="verdict")
    else:
        review = next(step for step in run.steps if step.code == "manual_review")
        transition_step(review, "succeeded"); review.progress = 100; review.started_at = review.started_at or verdict.reviewed_at; review.finished_at = verdict.reviewed_at; review.duration_ms = 0
        report_step = next(step for step in run.steps if step.code == "reporting")
        transition_step(report_step, "running"); report_step.started_at = beijing_now()
        report_result = generate_reports(db, run, step=report_step, reason="verdict")
        transition_step(report_step, "succeeded"); report_step.progress = 100; report_step.finished_at = beijing_now(); report_step.duration_ms = int((report_step.finished_at - report_step.started_at).total_seconds() * 1000)
        transition_run(run, "completed", source="api", actor_id=actor.id, reason="verdict submitted"); run.progress = 100; run.finished_at = beijing_now(); release_locks(db, run.id, "completed")
    write_audit(db, "run.verdict_submit", "test_run", run.id, actor, request, detail={"final_result": payload.final_result, "report_version": report_result["report_version"]}); db.commit(); broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress}); return verdict


@router.post("/runs/{run_id}/reports", response_model=typing.List[ArtifactOut])
def regenerate_reports(run_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> typing.List[Artifact]:
    run = load_run(db, run_id)
    if run.status != "completed": raise HTTPException(status_code=409, detail={"code": "RUN_NOT_COMPLETE", "message": "运行完成后才能生成报告"})
    report_step = next((step for step in run.steps if step.node_type == "report_generation"), None)
    result = generate_reports(db, run, step=report_step, reason="manual")
    artifacts = list(db.scalars(select(Artifact).where(Artifact.id.in_(result["artifact_ids"]))).all())
    write_audit(db, "report.regenerate", "test_run", run.id, actor, request, detail={"report_version": result["report_version"]}); db.commit(); return artifacts


@router.get("/runs/{run_id}/logs", response_model=typing.List[LogOut])
def list_run_logs(run_id: int, level: typing.Union[str, None] = None, source: typing.Union[str, None] = None, keyword: typing.Union[str, None] = None, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> typing.List[LogRecord]:
    query = select(LogRecord).where(LogRecord.run_id == run_id)
    if level: query = query.where(LogRecord.level == level.upper())
    if source: query = query.where(LogRecord.source == source)
    if keyword: query = query.where(LogRecord.message.contains(keyword))
    return list(db.scalars(query.order_by(LogRecord.created_at).limit(5000)).all())

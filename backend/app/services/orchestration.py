from __future__ import annotations

import typing
import asyncio
import gzip
import hashlib
import json
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import logger, redact
from app.core.observability import archive_observability_files
from app.core.time import as_beijing, beijing_now
from app.models import Artifact, AuditLog, LogRecord, Metric, Resource, ResourceLock, RunStep, ScenarioWorkflowNode, ScenarioWorkflowVersion, TestRun, TestScenario
from app.services.events import broker
from app.services.resource_relations import run_resource_ids
from app.services.statistics_execution import require_statistics_selection
from app.services.run_state import TERMINAL_RUN_STATUSES, transition_run, transition_step
from app.services.workflow_handlers import registry as workflow_handler_registry
from app.services.workflow_handlers.base import WorkflowExecutionContext
from app.services.workflows import WorkflowError, collect_slnic_merge_artifact

STEPS = [
    ("precheck", "环境预检"),
    ("wiring_confirmation", "人工接线确认"),
    ("capture_validation", "抓包验证"),
    ("environment_start", "环境启动"),
    ("order_execution", "发单执行"),
    ("collection", "停止与数据收集"),
    ("coco_parse", "Coco 解析"),
    ("statistics", "指标统计"),
    ("manual_review", "人工复核"),
    ("reporting", "报告生成"),
]

TERMINAL_STATUSES = TERMINAL_RUN_STATUSES


def _duration_ms(started_at: datetime, finished_at: datetime) -> int:
    return int((as_beijing(finished_at) - as_beijing(started_at)).total_seconds() * 1000)


def append_log(db: Session, run: TestRun, event: str, message: str, *, level: str = "INFO", step: typing.Union[RunStep, None] = None, source: str = "worker", detail: typing.Union[dict, None] = None, log_type: str = "run") -> LogRecord:
    safe_message = redact(message)
    record = LogRecord(
        log_type=log_type,
        level=level,
        event=event,
        message=safe_message,
        trace_id=run.trace_id,
        run_id=run.id,
        step_id=step.id if step else None,
        source=source,
        detail={key: redact(value) for key, value in (detail or {}).items()},
        is_redacted=True,
    )
    db.add(record)
    db.flush()
    broker.publish(run.id, {"type": "log", "data": {"id": record.id, "level": level, "event": event, "message": safe_message, "step_id": record.step_id, "created_at": beijing_now().isoformat()}})
    return record


def create_steps(run: TestRun) -> None:
    run.steps = [RunStep(code=code, name=name, position=index) for index, (code, name) in enumerate(STEPS, 1)]


def create_workflow_steps(run: TestRun, workflow: ScenarioWorkflowVersion) -> None:
    run.steps = [
        RunStep(
            workflow_node_id=node.id,
            code=node.node_key,
            name=node.name,
            node_type=node.node_type,
            config_snapshot=dict(node.config or {}),
            result_summary={},
            position=node.position,
        )
        for node in workflow.nodes
    ]


def acquire_locks(db: Session, run: TestRun, lease_minutes: int = 180) -> typing.Tuple[bool, typing.List[int]]:
    now = beijing_now()
    resource_ids = run_resource_ids(run)
    active = db.scalars(select(ResourceLock).where(and_(ResourceLock.resource_id.in_(resource_ids), ResourceLock.released_at.is_(None), ResourceLock.lease_expires_at > now))).all()
    conflicts = sorted({lock.resource_id for lock in active if lock.run_id != run.id})
    if conflicts:
        return False, conflicts
    for resource_id in resource_ids:
        existing = db.scalar(select(ResourceLock).where(ResourceLock.resource_id == resource_id, ResourceLock.run_id == run.id, ResourceLock.released_at.is_(None)))
        if not existing:
            db.add(ResourceLock(resource_id=resource_id, run_id=run.id, lease_expires_at=now + timedelta(minutes=lease_minutes)))
    db.flush()
    return True, []


def release_locks(db: Session, run_id: int, reason: str) -> int:
    locks = db.scalars(select(ResourceLock).where(ResourceLock.run_id == run_id, ResourceLock.released_at.is_(None))).all()
    now = beijing_now()
    for lock in locks:
        lock.released_at = now
        lock.release_reason = reason
    db.flush()
    return len(locks)


def renew_run_locks(db: Session, run_id: int, lease_minutes: int = 180) -> int:
    locks = list(
        db.scalars(
            select(ResourceLock).where(
                ResourceLock.run_id == run_id,
                ResourceLock.released_at.is_(None),
            )
        ).all()
    )
    lease_expires_at = beijing_now() + timedelta(minutes=lease_minutes)
    for lock in locks:
        lock.lease_expires_at = lease_expires_at
    db.flush()
    return len(locks)


def _load(db: Session, run_id: int) -> typing.Union[TestRun, None]:
    return db.scalar(select(TestRun).where(TestRun.id == run_id).options(selectinload(TestRun.steps), selectinload(TestRun.metrics), selectinload(TestRun.artifacts), selectinload(TestRun.verdict), selectinload(TestRun.resource_links)))


def _step(run: TestRun, code: str) -> RunStep:
    return next(step for step in run.steps if step.code == code)


async def _perform_step(db: Session, run: TestRun, code: str, run_status: str, duration: float = 0.08) -> None:
    step = _step(run, code)
    transition_run(run, run_status)
    transition_step(step, "running")
    step.started_at = beijing_now()
    started_clock = time.perf_counter()
    append_log(db, run, f"{code}.started", f"{step.name}开始", step=step)
    db.commit()
    broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})
    await asyncio.sleep(duration)
    db.refresh(run)
    if run.status == "cancelled":
        raise asyncio.CancelledError
    transition_step(step, "succeeded")
    step.progress = 100
    step.finished_at = beijing_now()
    step.duration_ms = int((time.perf_counter() - started_clock) * 1000)
    run.progress = min(90, step.position * 9)
    append_log(db, run, f"{code}.completed", f"{step.name}完成", step=step)
    db.commit()


def _create_sample_artifacts(db: Session, run: TestRun) -> None:
    directory = settings.artifact_root / run.business_code / str(run.plan_id) / str(run.scenario_id) / run.run_number / "collection"
    directory.mkdir(parents=True, exist_ok=True)
    samples = [82.1, 83.5, 81.9, 84.0, 82.7, 83.1, 82.4, 85.2, 81.8, 83.0]
    path = directory / "latency_samples.json"
    path.write_text(json.dumps(samples), encoding="utf-8")
    data = path.read_bytes()
    db.add(Artifact(run_id=run.id, step_id=_step(run, "collection").id, artifact_type="parsed_data", name=path.name, path=str(path), content_type="application/json", size=len(data), checksum=hashlib.sha256(data).hexdigest()))


def _calculate_metrics(db: Session, run: TestRun) -> None:
    values = [82.1, 83.5, 81.9, 84.0, 82.7, 83.1, 82.4, 85.2, 81.8, 83.0]
    mean = sum(values) / len(values)
    sorted_values = sorted(values)
    median = (sorted_values[4] + sorted_values[5]) / 2
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    entries = {"average": mean, "maximum": max(values), "minimum": min(values), "median": median, "stddev": variance ** 0.5, "sample_count": float(len(values)), "high_frequency_ratio": 0.8}
    for name, value in entries.items():
        db.add(Metric(run_id=run.id, name=name, value=value, unit="count" if name == "sample_count" else ("ratio" if name.endswith("ratio") else "us"), sample_count=len(values), detail={}))


async def start_run(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = _load(db, run_id)
        if not run or run.status not in {"draft", "resource_queue"}:
            return
        if run.workflow_version_id:
            db.close()
            await start_workflow_run(run_id)
            return
        acquired, conflicts = acquire_locks(db, run)
        if not acquired:
            transition_run(run, "resource_queue")
            run.queue_reason = f"资源被占用: {conflicts}"
            append_log(db, run, "run.queued", run.queue_reason, level="WARNING")
            db.commit()
            return
        transition_run(run, "precheck")
        run.started_at = run.started_at or beijing_now()
        run.queue_reason = None
        append_log(db, run, "run.started", "测速运行已启动")
        db.commit()
        if _step(run, "precheck").status != "succeeded":
            await _perform_step(db, run, "precheck", "precheck")
        wiring = _step(run, "wiring_confirmation")
        if wiring.status == "succeeded":
            transition_run(run, "awaiting_wiring")
            db.commit()
            await continue_after_wiring(run.id)
            return
        transition_step(wiring, "waiting")
        transition_run(run, "awaiting_wiring")
        append_log(db, run, "wiring.waiting", "请完成机房接线并在页面确认", step=wiring)
        db.commit()
        broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})
    except Exception as exc:
        logger.exception("run_start_failed", run_id=run_id)
        run = _load(db, run_id)
        if run:
            transition_run(run, "precheck_failed")
            run.error_code = "PRECHECK_FAILED"
            run.error_message = redact(str(exc))
            run.finished_at = beijing_now()
            append_log(db, run, "run.failed", str(exc), level="ERROR")
            release_locks(db, run.id, "precheck_failed")
            db.commit()
    finally:
        db.close()


async def start_workflow_run(run_id: int, step_id: typing.Optional[int] = None) -> None:
    db = SessionLocal()
    try:
        run = _load(db, run_id)
        if not run or not run.workflow_version_id:
            return
        if step_id is None:
            if run.status not in {"draft", "resource_queue"}:
                return
            acquired, conflicts = acquire_locks(db, run)
            if not acquired:
                transition_run(run, "resource_queue")
                run.queue_reason = f"资源被占用: {conflicts}"
                append_log(db, run, "run.queued", run.queue_reason, level="WARNING")
                db.commit()
                return
            run.started_at = run.started_at or beijing_now()
            run.queue_reason = None
            if run.steps:
                transition_run(run, "awaiting_step_start")
                append_log(db, run, "run.started", "工作流已启动，等待手动开始第一个节点")
            else:
                transition_run(run, "completed")
                run.progress = 100
                run.finished_at = beijing_now()
                release_locks(db, run.id, "completed")
                append_log(db, run, "run.completed", "工作流运行完成")
            db.commit()
            broker.publish(
                run.id,
                {"type": "status", "status": run.status, "progress": run.progress},
            )
            return
        if run.status != "running":
            return
        workflow = db.get(ScenarioWorkflowVersion, run.workflow_version_id)
        scenario = db.get(TestScenario, run.scenario_id)
        if not workflow or not scenario:
            raise WorkflowError("WORKFLOW_NOT_FOUND", "运行关联的工作流不存在", 409)
        nodes = {node.id: node for node in db.scalars(select(ScenarioWorkflowNode).where(ScenarioWorkflowNode.workflow_version_id == workflow.id)).all()}
        resources = list(db.scalars(select(Resource).where(Resource.id.in_(run_resource_ids(run)))).all())
        run_resources = {item.resource_type: item for item in resources}
        total = max(1, len(run.steps))
        for step in run.steps:
            if step.id != step_id:
                continue
            node = nodes.get(step.workflow_node_id)
            if not node:
                raise WorkflowError("WORKFLOW_NODE_NOT_FOUND", f"节点 {step.name} 不存在", 409)
            context = WorkflowExecutionContext(
                db=db,
                run=run,
                step=step,
                node=node,
                scenario=scenario,
                workflow=workflow,
                resources=run_resources,
                append_log=append_log,
            )
            step.result_summary = await workflow_handler_registry.execute(node.node_type, context)
            executed_at = beijing_now()
            transition_step(step, "waiting")
            step.progress = 100
            step.duration_ms = _duration_ms(step.started_at, executed_at)
            transition_run(run, "awaiting_step_completion")
            run.progress = int((step.position - 1) * 100 / total)
            append_log(db, run, "workflow.step_executed", f"{step.name}执行结束，等待手动完成", step=step)
            db.commit()
            broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})
            return
        raise WorkflowError("WORKFLOW_STEP_NOT_FOUND", "运行步骤不存在", 404)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.exception("workflow_step_execution_failed", run_id=run_id, step_id=step_id)
        run = _load(db, run_id)
        if run:
            failed = next((step for step in run.steps if step.status == "running"), None)
            if failed:
                transition_step(failed, "failed")
                failed.error_message = redact(str(exc))
                failed.finished_at = beijing_now()
                started_at = failed.started_at or failed.finished_at
                failed.duration_ms = _duration_ms(started_at, failed.finished_at)
                transition_run(run, "awaiting_step_retry")
                run.error_code = None
                run.error_message = None
                run.finished_at = None
                append_log(
                    db,
                    run,
                    "workflow.step_failed",
                    str(exc),
                    level="ERROR",
                    step=failed,
                    detail={"error_code": getattr(exc, "code", "WORKFLOW_EXECUTION_FAILED")},
                )
            else:
                transition_run(run, "execution_failed")
                run.error_code = getattr(exc, "code", "WORKFLOW_EXECUTION_FAILED")
                run.error_message = redact(str(exc))
                run.finished_at = beijing_now()
                append_log(db, run, "run.failed", str(exc), level="ERROR")
                release_locks(db, run.id, run.status)
            db.commit()
            broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})
    finally:
        db.close()


def begin_workflow_step(
    db: Session,
    run: TestRun,
    step_id: int,
    *,
    retry: bool = False,
) -> RunStep:
    expected_status = "awaiting_step_retry" if retry else "awaiting_step_start"
    if not run.workflow_version_id or run.status != expected_status:
        raise WorkflowError("INVALID_TRANSITION", "当前运行不能执行该节点", 409)
    current = next((item for item in run.steps if item.status != "succeeded"), None)
    if not current or current.id != step_id:
        raise WorkflowError("INVALID_WORKFLOW_STEP", "只能操作当前节点", 409)
    expected_step_status = "failed" if retry else "pending"
    if current.status != expected_step_status:
        raise WorkflowError("INVALID_TRANSITION", "当前节点状态不能执行此操作", 409)
    if current.node_type == "data_statistics":
        require_statistics_selection(db, run, current)
    if retry:
        current.retry_count += 1
    transition_step(current, "running")
    current.progress = 0
    current.error_message = None
    current.finished_at = None
    current.started_at = beijing_now()
    transition_run(run, "running")
    run.error_code = None
    run.error_message = None
    append_log(
        db,
        run,
        "workflow.step_retried" if retry else "workflow.step_started",
        f"{current.name}{'重试' if retry else '开始'}",
        step=current,
        detail={"retry_count": current.retry_count},
    )
    db.flush()
    broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})
    return current


async def complete_workflow_step(db: Session, run: TestRun, step_id: int, actor_id: int) -> None:
    if not run.workflow_version_id or run.status != "awaiting_step_completion":
        raise WorkflowError("INVALID_TRANSITION", "当前运行没有待完成的节点", 409)
    step = next((item for item in run.steps if item.id == step_id), None)
    current = next((item for item in run.steps if item.status != "succeeded"), None)
    if not step or not current or current.id != step.id or step.status != "waiting":
        raise WorkflowError("INVALID_WORKFLOW_STEP", "只能完成当前已执行的节点", 409)
    if (
        step.node_type == "order_preparation"
        and (step.result_summary or {}).get("order_action_status") in {"dispatching", "unknown"}
    ):
        raise WorkflowError("ORDER_ACTION_UNRESOLVED", "发单动作结果尚未确认，不能完成节点", 409)
    now = beijing_now()
    if (
        step.node_type == "slnic_merge_capture"
        and (step.result_summary or {}).get("mode") == "terminal"
        and not (step.result_summary or {}).get("artifact_id")
    ):
        slnic_resource = db.scalar(select(Resource).where(
            Resource.id.in_(run_resource_ids(run)),
            Resource.resource_type == "slnic",
        ))
        if not slnic_resource:
            raise WorkflowError("SLNIC_RESOURCE_REQUIRED", "运行资源缺少已启用的 SLNIC 节点", 409)
        artifact_summary = await collect_slnic_merge_artifact(db, run, step, slnic_resource)
        step.result_summary = {
            **(step.result_summary or {}),
            **artifact_summary,
        }
        db.expire(run, ["artifacts"])
    transition_step(step, "succeeded")
    step.progress = 100
    step.finished_at = now
    if step.node_type == "wiring_confirmation":
        step.result_summary = {
            **(step.result_summary or {}),
            "confirmed": True,
            "confirmed_by": actor_id,
            "confirmed_at": now.isoformat(),
        }
    run.progress = int(step.position * 100 / max(1, len(run.steps)))
    next_step = next((item for item in run.steps if item.status != "succeeded"), None)
    if next_step:
        transition_run(run, "awaiting_step_start")
        append_log(db, run, "workflow.step_completed", f"{step.name}已完成", step=step)
    else:
        transition_run(run, "completed")
        run.progress = 100
        run.finished_at = now
        release_locks(db, run.id, "completed")
        append_log(db, run, "run.completed", "工作流运行完成", step=step)
    db.flush()
    broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})


async def confirm_workflow_step(db: Session, run: TestRun, step_id: int, actor_id: int) -> None:
    await complete_workflow_step(db, run, step_id, actor_id)


async def continue_after_wiring(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = _load(db, run_id)
        if not run or run.status != "awaiting_wiring":
            return
        wiring = _step(run, "wiring_confirmation")
        transition_step(wiring, "succeeded"); wiring.progress = 100; wiring.started_at = wiring.started_at or beijing_now(); wiring.finished_at = beijing_now(); wiring.duration_ms = 0
        append_log(db, run, "wiring.confirmed", "人工接线已确认", step=wiring)
        db.commit()
        phases = [
            ("capture_validation", "capture_validation"),
            ("environment_start", "environment_start"),
            ("order_execution", "order_execution"),
            ("collection", "collection"),
            ("coco_parse", "coco_parse"),
            ("statistics", "statistics"),
        ]
        for code, status in phases:
            if _step(run, code).status == "succeeded":
                continue
            await _perform_step(db, run, code, status)
            if code == "collection":
                _create_sample_artifacts(db, run); db.commit()
            if code == "statistics":
                _calculate_metrics(db, run); db.commit()
        review = _step(run, "manual_review")
        transition_step(review, "waiting")
        transition_run(run, "awaiting_review")
        run.progress = 90
        append_log(db, run, "review.waiting", "自动分析完成，等待人工复核", step=review)
        db.commit()
        broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.exception("run_execution_failed", run_id=run_id)
        run = _load(db, run_id)
        if run:
            failed_step = next((step for step in run.steps if step.status == "running"), None)
            if failed_step:
                transition_step(failed_step, "failed"); failed_step.error_message = redact(str(exc)); failed_step.finished_at = beijing_now()
            transition_run(run, "parse_failed" if failed_step and failed_step.code == "coco_parse" else "execution_failed")
            run.error_code = "EXECUTION_FAILED"; run.error_message = redact(str(exc)); run.finished_at = beijing_now()
            append_log(db, run, "run.failed", str(exc), level="ERROR", step=failed_step)
            release_locks(db, run.id, run.status)
            db.commit()
    finally:
        db.close()


def cancel_run(
    db: Session,
    run: TestRun,
    reason: str = "user_cancelled",
    actor_id: typing.Optional[int] = None,
) -> None:
    if run.status in TERMINAL_STATUSES:
        return
    transition_run(
        run,
        "cancelled",
        source="api" if actor_id is not None else "service",
        actor_id=actor_id,
        reason=reason,
    )
    run.finished_at = beijing_now()
    for step in run.steps:
        if step.status in {"running", "waiting"}:
            transition_step(step, "cancelled"); step.finished_at = beijing_now()
    append_log(db, run, "run.cancelled", "运行已取消，安全清理已触发", level="WARNING", detail={"reason": reason})
    release_locks(db, run.id, reason)
    db.commit()
    broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})


def reclaim_expired_locks(db: Session) -> int:
    now = beijing_now()
    locks = db.scalars(select(ResourceLock).where(ResourceLock.released_at.is_(None), ResourceLock.lease_expires_at <= now)).all()
    for lock in locks:
        lock.released_at = now; lock.release_reason = "lease_expired"
    db.commit()
    return len(locks)


def expire_timed_out_runs(db: Session) -> int:
    now = beijing_now()
    runs = list(
        db.scalars(
            select(TestRun)
            .where(
                TestRun.timeout_at.is_not(None),
                TestRun.timeout_at <= now,
                TestRun.status.not_in(TERMINAL_RUN_STATUSES),
            )
            .order_by(TestRun.timeout_at, TestRun.id)
        ).all()
    )
    cleanup_steps: list[tuple[int, int]] = []
    for run in runs:
        transition_run(
            run,
            "timed_out",
            source="scheduler",
            reason="run timeout deadline exceeded",
        )
        run.finished_at = now
        run.error_code = "RUN_TIMED_OUT"
        run.error_message = "运行超过设定时限"
        for step in run.steps:
            if step.node_type == "order_preparation" and (step.result_summary or {}).get("process_started"):
                cleanup_steps.append((run.id, step.id))
            if step.status in {"pending", "running", "waiting", "failed"}:
                transition_step(step, "cancelled")
                step.finished_at = step.finished_at or now
        append_log(
            db,
            run,
            "run.timed_out",
            "运行超过设定时限，资源已释放",
            level="WARNING",
        )
        release_locks(db, run.id, "timed_out")
    db.commit()
    for run in runs:
        broker.publish(
            run.id,
            {"type": "status", "status": run.status, "progress": run.progress},
        )
    if cleanup_steps:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop:
            from app.services.order_sessions import cleanup_order_step_by_ids

            for run_id, step_id in cleanup_steps:
                loop.create_task(cleanup_order_step_by_ids(run_id, step_id))
    return len(runs)


def queued_run_ids(db: Session, limit: int = 20) -> typing.List[int]:
    return list(
        db.scalars(
            select(TestRun.id)
            .where(TestRun.status == "resource_queue")
            .order_by(TestRun.created_at)
            .limit(limit)
        ).all()
    )


def archive_and_clean_logs(db: Session) -> typing.Dict[str, int]:
    now = beijing_now()
    log_cutoff = now - timedelta(days=settings.app_log_retention_days)
    observability_cutoff = now - timedelta(days=settings.observability_hot_retention_days)
    audit_cutoff = now - timedelta(days=settings.audit_log_retention_days)
    observability_types = ["access", "sql", "websocket"]
    old_logs = db.scalars(
        select(LogRecord).where(
            or_(
                and_(
                    LogRecord.log_type.in_(observability_types),
                    LogRecord.created_at < observability_cutoff,
                ),
                and_(
                    LogRecord.log_type.notin_(observability_types),
                    LogRecord.created_at < log_cutoff,
                ),
            )
        )
    ).all()
    old_audits = db.scalars(select(AuditLog).where(AuditLog.created_at < audit_cutoff)).all()
    archive_dir = settings.log_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    if old_logs:
        path = archive_dir / f"log-records-{now:%Y%m%d%H%M%S}.jsonl.gz"
        with gzip.open(path, "wt", encoding="utf-8") as output:
            for record in old_logs:
                output.write(json.dumps({"id": record.id, "type": record.log_type, "level": record.level, "event": record.event, "message": record.message, "trace_id": record.trace_id, "created_at": record.created_at.isoformat()}, ensure_ascii=False) + "\n")
                db.delete(record)
    if old_audits:
        path = archive_dir / f"audit-logs-{now:%Y%m%d%H%M%S}.jsonl.gz"
        with gzip.open(path, "wt", encoding="utf-8") as output:
            for record in old_audits:
                output.write(json.dumps({"id": record.id, "actor_id": record.actor_id, "action": record.action, "object_type": record.object_type, "object_id": record.object_id, "result": record.result, "trace_id": record.trace_id, "created_at": record.created_at.isoformat()}, ensure_ascii=False) + "\n")
                db.delete(record)
    observability_files = archive_observability_files()
    db.add(AuditLog(action="retention.cleanup", object_type="log_records", result="success", trace_id=str(uuid4()), detail={"logs_archived": len(old_logs), "audits_archived": len(old_audits), **observability_files}))
    db.commit()
    return {"logs_archived": len(old_logs), "audits_archived": len(old_audits), **observability_files}

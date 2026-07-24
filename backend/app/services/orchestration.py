from __future__ import annotations

import typing
import asyncio
import gzip
import hashlib
import json
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import and_, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import logger, redact
from app.models import Artifact, AuditLog, LogRecord, Metric, Resource, ResourceLock, RunStep, ScenarioWorkflowNode, ScenarioWorkflowVersion, TestRun, TestScenario
from app.services.events import broker
from app.services.workflows import (
    SLNIC_NODE_TYPES,
    WorkflowError,
    capture_database,
    capture_server,
    execute_slnic_node,
    prepare_order_node,
)

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

TERMINAL_STATUSES = {"completed", "precheck_failed", "execution_failed", "parse_failed", "cancelled", "timed_out"}


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
    broker.publish(run.id, {"type": "log", "data": {"id": record.id, "level": level, "event": event, "message": safe_message, "step_id": record.step_id, "created_at": datetime.now(timezone.utc).isoformat()}})
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
    now = datetime.now(timezone.utc)
    active = db.scalars(select(ResourceLock).where(and_(ResourceLock.resource_id.in_(run.resource_ids), ResourceLock.released_at.is_(None), ResourceLock.lease_expires_at > now))).all()
    conflicts = sorted({lock.resource_id for lock in active if lock.run_id != run.id})
    if conflicts:
        return False, conflicts
    for resource_id in run.resource_ids:
        existing = db.scalar(select(ResourceLock).where(ResourceLock.resource_id == resource_id, ResourceLock.run_id == run.id, ResourceLock.released_at.is_(None)))
        if not existing:
            db.add(ResourceLock(resource_id=resource_id, run_id=run.id, lease_expires_at=now + timedelta(minutes=lease_minutes)))
    db.flush()
    return True, []


def release_locks(db: Session, run_id: int, reason: str) -> int:
    locks = db.scalars(select(ResourceLock).where(ResourceLock.run_id == run_id, ResourceLock.released_at.is_(None))).all()
    now = datetime.now(timezone.utc)
    for lock in locks:
        lock.released_at = now
        lock.release_reason = reason
    db.flush()
    return len(locks)


def _load(db: Session, run_id: int) -> typing.Union[TestRun, None]:
    return db.scalar(select(TestRun).where(TestRun.id == run_id).options(selectinload(TestRun.steps), selectinload(TestRun.metrics), selectinload(TestRun.artifacts), selectinload(TestRun.verdict)))


def _step(run: TestRun, code: str) -> RunStep:
    return next(step for step in run.steps if step.code == code)


async def _perform_step(db: Session, run: TestRun, code: str, run_status: str, duration: float = 0.08) -> None:
    step = _step(run, code)
    run.status = run_status
    step.status = "running"
    step.started_at = datetime.now(timezone.utc)
    started_clock = time.perf_counter()
    append_log(db, run, f"{code}.started", f"{step.name}开始", step=step)
    db.commit()
    broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})
    await asyncio.sleep(duration)
    db.refresh(run)
    if run.status == "cancelled":
        raise asyncio.CancelledError
    step.status = "succeeded"
    step.progress = 100
    step.finished_at = datetime.now(timezone.utc)
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
            run.status = "resource_queue"
            run.queue_reason = f"资源被占用: {conflicts}"
            append_log(db, run, "run.queued", run.queue_reason, level="WARNING")
            db.commit()
            return
        run.status = "precheck"
        run.started_at = run.started_at or datetime.now(timezone.utc)
        run.queue_reason = None
        append_log(db, run, "run.started", "测速运行已启动")
        db.commit()
        if _step(run, "precheck").status != "succeeded":
            await _perform_step(db, run, "precheck", "precheck")
        wiring = _step(run, "wiring_confirmation")
        if wiring.status == "succeeded":
            run.status = "awaiting_wiring"
            db.commit()
            await continue_after_wiring(run.id)
            return
        wiring.status = "waiting"
        run.status = "awaiting_wiring"
        append_log(db, run, "wiring.waiting", "请完成机房接线并在页面确认", step=wiring)
        db.commit()
        broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})
    except Exception as exc:
        logger.exception("run_start_failed", run_id=run_id)
        run = _load(db, run_id)
        if run:
            run.status = "precheck_failed"
            run.error_code = "PRECHECK_FAILED"
            run.error_message = redact(str(exc))
            run.finished_at = datetime.now(timezone.utc)
            append_log(db, run, "run.failed", str(exc), level="ERROR")
            release_locks(db, run.id, "precheck_failed")
            db.commit()
    finally:
        db.close()


async def start_workflow_run(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = _load(db, run_id)
        if not run or not run.workflow_version_id or run.status not in {"draft", "resource_queue"}:
            return
        acquired, conflicts = acquire_locks(db, run)
        if not acquired:
            run.status = "resource_queue"
            run.queue_reason = f"资源被占用: {conflicts}"
            append_log(db, run, "run.queued", run.queue_reason, level="WARNING")
            db.commit()
            return
        workflow = db.get(ScenarioWorkflowVersion, run.workflow_version_id)
        scenario = db.get(TestScenario, run.scenario_id)
        if not workflow or not scenario:
            raise WorkflowError("WORKFLOW_NOT_FOUND", "运行关联的工作流不存在", 409)
        nodes = {node.id: node for node in db.scalars(select(ScenarioWorkflowNode).where(ScenarioWorkflowNode.workflow_version_id == workflow.id)).all()}
        resources = list(db.scalars(select(Resource).where(Resource.id.in_(run.resource_ids))).all())
        run_resources = {item.resource_type: item for item in resources}
        run.started_at = run.started_at or datetime.now(timezone.utc)
        run.queue_reason = None
        append_log(db, run, "run.started", "工作流运行已启动")
        db.commit()
        total = max(1, len(run.steps))
        for step in run.steps:
            if step.status == "succeeded":
                continue
            node = nodes.get(step.workflow_node_id)
            if not node:
                raise WorkflowError("WORKFLOW_NODE_NOT_FOUND", f"节点 {step.name} 不存在", 409)
            if node.node_type == "wiring_confirmation":
                step.status = "waiting"
                step.started_at = step.started_at or datetime.now(timezone.utc)
                step.result_summary = {"diagram": "placeholder", "confirmed": False}
                run.status = "awaiting_confirmation"
                run.progress = int((step.position - 1) * 100 / total)
                append_log(db, run, "workflow.confirmation_waiting", f"{step.name}等待人工确认", step=step)
                db.commit()
                broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})
                return
            step.status = "running"
            step.started_at = datetime.now(timezone.utc)
            run.status = "running"
            append_log(db, run, "workflow.step_started", f"{step.name}开始", step=step)
            db.commit()
            if node.node_type == "server_config":
                snapshots = await capture_server(db, scenario, workflow, node, scope="run", actor_id=run.created_by, run_id=run.id, run_step_id=step.id, run_resources=run_resources)
                failed = [item for item in snapshots if item.status == "failed"]
                step.result_summary = {"snapshot_ids": [item.id for item in snapshots], "sources": len(snapshots), "failed": len(failed)}
                if failed:
                    raise WorkflowError("CONFIG_CAPTURE_FAILED", "服务器配置采集不完整", 409)
            elif node.node_type == "database_config":
                snapshots = await capture_database(db, scenario, workflow, node, scope="run", actor_id=run.created_by, run_id=run.id, run_step_id=step.id, run_resources=run_resources)
                failed = [item for item in snapshots if item.status == "failed"]
                step.result_summary = {"snapshot_ids": [item.id for item in snapshots], "sources": len(snapshots), "failed": len(failed)}
                if failed:
                    raise WorkflowError("CONFIG_CAPTURE_FAILED", "数据库配置采集不完整", 409)
            elif node.node_type == "order_preparation":
                step.result_summary = await prepare_order_node(db, workflow, node, run_resources)
            elif node.node_type in SLNIC_NODE_TYPES:
                step.result_summary = await execute_slnic_node(db, run, step, node, run_resources)
            else:
                raise WorkflowError("WORKFLOW_NODE_UNSUPPORTED", f"不支持节点类型 {node.node_type}", 409)
            step.status = "succeeded"
            step.progress = 100
            step.finished_at = datetime.now(timezone.utc)
            step.duration_ms = int((step.finished_at - step.started_at).total_seconds() * 1000)
            run.progress = int(step.position * 100 / total)
            append_log(db, run, "workflow.step_completed", f"{step.name}完成", step=step)
            db.commit()
        run.status = "completed"
        run.progress = 100
        run.finished_at = datetime.now(timezone.utc)
        release_locks(db, run.id, "completed")
        append_log(db, run, "run.completed", "工作流运行完成")
        db.commit()
        broker.publish(run.id, {"type": "status", "status": "completed", "progress": 100})
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.exception("workflow_run_failed", run_id=run_id)
        run = _load(db, run_id)
        if run:
            failed = next((step for step in run.steps if step.status == "running"), None)
            if failed:
                failed.status = "failed"
                failed.error_message = redact(str(exc))
                failed.finished_at = datetime.now(timezone.utc)
            run.status = "execution_failed"
            run.error_code = getattr(exc, "code", "WORKFLOW_EXECUTION_FAILED")
            run.error_message = redact(str(exc))
            run.finished_at = datetime.now(timezone.utc)
            append_log(db, run, "run.failed", str(exc), level="ERROR", step=failed)
            release_locks(db, run.id, "execution_failed")
            db.commit()
            broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})
    finally:
        db.close()


def confirm_workflow_step(db: Session, run: TestRun, step_id: int, actor_id: int) -> None:
    if not run.workflow_version_id or run.status != "awaiting_confirmation":
        raise WorkflowError("INVALID_TRANSITION", "当前运行不等待人工确认", 409)
    step = next((item for item in run.steps if item.id == step_id), None)
    if not step or step.node_type != "wiring_confirmation" or step.status != "waiting":
        raise WorkflowError("INVALID_CONFIRMATION_STEP", "当前节点不能确认", 409)
    now = datetime.now(timezone.utc)
    step.status = "succeeded"
    step.progress = 100
    step.finished_at = now
    started_at = step.started_at or now
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    step.duration_ms = int((now - started_at).total_seconds() * 1000)
    step.result_summary = {"diagram": "placeholder", "confirmed": True, "confirmed_by": actor_id, "confirmed_at": now.isoformat()}
    run.status = "resource_queue"
    run.progress = int(step.position * 100 / max(1, len(run.steps)))
    append_log(db, run, "workflow.confirmed", f"{step.name}已确认", step=step)
    db.flush()


async def continue_after_wiring(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = _load(db, run_id)
        if not run or run.status != "awaiting_wiring":
            return
        wiring = _step(run, "wiring_confirmation")
        wiring.status = "succeeded"; wiring.progress = 100; wiring.started_at = wiring.started_at or datetime.now(timezone.utc); wiring.finished_at = datetime.now(timezone.utc); wiring.duration_ms = 0
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
        review.status = "waiting"
        run.status = "awaiting_review"
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
                failed_step.status = "failed"; failed_step.error_message = redact(str(exc)); failed_step.finished_at = datetime.now(timezone.utc)
            run.status = "parse_failed" if failed_step and failed_step.code == "coco_parse" else "execution_failed"
            run.error_code = "EXECUTION_FAILED"; run.error_message = redact(str(exc)); run.finished_at = datetime.now(timezone.utc)
            append_log(db, run, "run.failed", str(exc), level="ERROR", step=failed_step)
            release_locks(db, run.id, run.status)
            db.commit()
    finally:
        db.close()


def cancel_run(db: Session, run: TestRun, reason: str = "user_cancelled") -> None:
    if run.status in TERMINAL_STATUSES:
        return
    run.status = "cancelled"
    run.finished_at = datetime.now(timezone.utc)
    for step in run.steps:
        if step.status in {"running", "waiting"}:
            step.status = "cancelled"; step.finished_at = datetime.now(timezone.utc)
    append_log(db, run, "run.cancelled", "运行已取消，安全清理已触发", level="WARNING", detail={"reason": reason})
    release_locks(db, run.id, reason)
    db.commit()
    broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})


def reclaim_expired_locks(db: Session) -> int:
    now = datetime.now(timezone.utc)
    locks = db.scalars(select(ResourceLock).where(ResourceLock.released_at.is_(None), ResourceLock.lease_expires_at <= now)).all()
    for lock in locks:
        lock.released_at = now; lock.release_reason = "lease_expired"
    db.commit()
    return len(locks)


def expire_timed_out_runs(db: Session) -> int:
    now = datetime.now(timezone.utc)
    runs = db.scalars(
        select(TestRun).where(
            TestRun.timeout_at <= now,
            TestRun.status.notin_(TERMINAL_STATUSES | {"awaiting_review"}),
        ).options(selectinload(TestRun.steps))
    ).all()
    for run in runs:
        run.status = "timed_out"
        run.finished_at = now
        run.error_code = "RUN_TIMEOUT"
        run.error_message = "运行超过配置的最长执行时间"
        for step in run.steps:
            if step.status in {"running", "waiting"}:
                step.status = "cancelled"
                step.finished_at = now
        append_log(db, run, "run.timed_out", run.error_message, level="ERROR")
        release_locks(db, run.id, "timed_out")
    db.commit()
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
    now = datetime.now(timezone.utc)
    log_cutoff = now - timedelta(days=settings.app_log_retention_days)
    audit_cutoff = now - timedelta(days=settings.audit_log_retention_days)
    old_logs = db.scalars(select(LogRecord).where(LogRecord.created_at < log_cutoff)).all()
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
    db.add(AuditLog(action="retention.cleanup", object_type="log_records", result="success", trace_id=str(uuid4()), detail={"logs_archived": len(old_logs), "audits_archived": len(old_audits)}))
    db.commit()
    return {"logs_archived": len(old_logs), "audits_archived": len(old_audits)}

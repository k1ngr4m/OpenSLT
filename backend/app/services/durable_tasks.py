from __future__ import annotations

import asyncio
import os
import socket
import typing
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import logger, redact
from app.core.time import beijing_now
from app.models import DurableTask, TestRun
from app.services.run_state import TERMINAL_RUN_STATUSES


WORKER_ID = "%s:%s:%s" % (socket.gethostname(), os.getpid(), uuid4().hex[:8])
TASK_TYPES = frozenset({"start_run", "continue_after_wiring", "start_workflow_step", "svn_sync", "smart_case_generate"})


def _payload_run_id(payload: typing.Mapping[str, typing.Any]) -> typing.Optional[int]:
    value = payload.get("run_id")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def enqueue_task(
    db: Session,
    task_type: str,
    payload: typing.Dict[str, typing.Any],
    idempotency_key: str,
    *,
    max_attempts: int = 3,
    available_at: typing.Optional[datetime] = None,
    reactivate: bool = False,
) -> DurableTask:
    if task_type not in TASK_TYPES:
        raise ValueError("unsupported durable task type: %s" % task_type)
    existing = db.scalar(
        select(DurableTask).where(DurableTask.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if existing.run_id is None:
            existing.run_id = _payload_run_id(payload)
        if reactivate and existing.status in {"succeeded", "failed"}:
            existing.status = "queued"
            existing.attempts = 0
            existing.available_at = available_at or beijing_now()
            existing.lease_expires_at = None
            existing.locked_by = None
            existing.last_error = None
            existing.finished_at = None
            db.flush()
        return existing
    task = DurableTask(
        task_type=task_type,
        payload=dict(payload),
        run_id=_payload_run_id(payload),
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
        available_at=available_at or beijing_now(),
    )
    db.add(task)
    db.flush()
    return task


def _claimable(now: datetime):
    return and_(
        DurableTask.attempts < DurableTask.max_attempts,
        DurableTask.available_at <= now,
        or_(
            DurableTask.status == "queued",
            and_(
                DurableTask.status == "running",
                DurableTask.lease_expires_at.is_not(None),
                DurableTask.lease_expires_at <= now,
            ),
        ),
    )


def claim_task(
    db: Session,
    task_id: int,
    worker_id: str = WORKER_ID,
    lease_seconds: typing.Optional[int] = None,
) -> bool:
    now = beijing_now()
    lease = lease_seconds or settings.task_lease_seconds
    result = db.execute(
        update(DurableTask)
        .where(DurableTask.id == task_id, _claimable(now))
        .values(
            status="running",
            attempts=DurableTask.attempts + 1,
            locked_by=worker_id,
            lease_expires_at=now + timedelta(seconds=lease),
            started_at=now,
            finished_at=None,
        )
    )
    db.commit()
    return result.rowcount == 1


def claim_due_tasks(
    db: Session,
    *,
    limit: int = 20,
    worker_id: str = WORKER_ID,
) -> typing.List[int]:
    now = beijing_now()
    candidate_ids = list(
        db.scalars(
            select(DurableTask.id)
            .where(_claimable(now))
            .order_by(DurableTask.available_at, DurableTask.id)
            .limit(limit)
        ).all()
    )
    return [task_id for task_id in candidate_ids if claim_task(db, task_id, worker_id)]


def renew_task_lease(
    db: Session,
    task_id: int,
    worker_id: str = WORKER_ID,
    lease_seconds: typing.Optional[int] = None,
) -> bool:
    lease = lease_seconds or settings.task_lease_seconds
    result = db.execute(
        update(DurableTask)
        .where(
            DurableTask.id == task_id,
            DurableTask.status == "running",
            DurableTask.locked_by == worker_id,
        )
        .values(lease_expires_at=beijing_now() + timedelta(seconds=lease))
    )
    db.commit()
    return result.rowcount == 1


def recover_abandoned_tasks(db: Session) -> int:
    now = beijing_now()
    result = db.execute(
        update(DurableTask)
        .where(
            DurableTask.status == "running",
            DurableTask.lease_expires_at.is_not(None),
            DurableTask.lease_expires_at <= now,
            DurableTask.attempts < DurableTask.max_attempts,
        )
        .values(status="queued", locked_by=None, lease_expires_at=None, available_at=now)
    )
    active_start_run_ids = set(
        db.scalars(
            select(DurableTask.run_id).where(
                DurableTask.task_type == "start_run",
                DurableTask.status.in_({"queued", "running"}),
                DurableTask.run_id.is_not(None),
            )
        ).all()
    )
    queued_runs = list(
        db.scalars(
            select(TestRun).where(
                TestRun.status == "resource_queue",
                TestRun.status.not_in(TERMINAL_RUN_STATUSES),
            )
        ).all()
    )
    for run in queued_runs:
        if run.id in active_start_run_ids:
            continue
        enqueue_task(
            db,
            "start_run",
            {"run_id": run.id},
            "recovery:start-run:%s:v%s" % (run.id, run.status_version),
        )
    db.commit()
    return result.rowcount


async def _execute_payload(task: DurableTask) -> None:
    from app.services import orchestration

    if task.task_type == "smart_case_generate":
        from app.services.smart_case_generation import execute_smart_case_generation

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, execute_smart_case_generation, int(task.payload["generation_id"]))
    elif task.task_type == "svn_sync":
        from app.services.svn_knowledge import execute_svn_sync

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, execute_svn_sync, int(task.payload["source_id"]))
    elif task.task_type == "start_run":
        run_id = int(task.payload["run_id"])
        await orchestration.start_run(run_id)
    elif task.task_type == "continue_after_wiring":
        run_id = int(task.payload["run_id"])
        await orchestration.continue_after_wiring(run_id)
    elif task.task_type == "start_workflow_step":
        run_id = int(task.payload["run_id"])
        await orchestration.start_workflow_run(run_id, int(task.payload["step_id"]))
    else:
        raise ValueError("unsupported durable task type: %s" % task.task_type)


async def _heartbeat(task_id: int, worker_id: str, run_id: typing.Optional[int]) -> None:
    from app.services.orchestration import renew_run_locks

    while True:
        await asyncio.sleep(settings.task_heartbeat_seconds)
        db = SessionLocal()
        try:
            if not renew_task_lease(db, task_id, worker_id):
                return
            if run_id is not None:
                renew_run_locks(db, run_id)
                db.commit()
        except Exception:
            db.rollback()
            logger.exception("durable_task_heartbeat_failed", task_id=task_id)
        finally:
            db.close()


async def execute_claimed_task(task_id: int, worker_id: str = WORKER_ID) -> None:
    db = SessionLocal()
    try:
        task = db.get(DurableTask, task_id)
        if not task or task.status != "running" or task.locked_by != worker_id:
            return
        run_id = task.payload.get("run_id")
        run_id = int(run_id) if run_id is not None else None
        snapshot = DurableTask(
            id=task.id,
            task_type=task.task_type,
            payload=dict(task.payload),
            attempts=task.attempts,
            max_attempts=task.max_attempts,
        )
    finally:
        db.close()

    heartbeat = asyncio.create_task(_heartbeat(task_id, worker_id, run_id))
    error = None
    try:
        await _execute_payload(snapshot)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error = redact(str(exc))
        logger.exception("durable_task_failed", task_id=task_id, task_type=snapshot.task_type)
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass

    db = SessionLocal()
    try:
        task = db.get(DurableTask, task_id)
        if not task or task.status != "running" or task.locked_by != worker_id:
            return
        now = beijing_now()
        if error is None:
            task.status = "succeeded"
            task.finished_at = now
        elif task.attempts >= task.max_attempts:
            task.status = "failed"
            task.finished_at = now
            task.last_error = error
        else:
            task.status = "queued"
            task.available_at = now + timedelta(seconds=min(60, 2 ** task.attempts))
            task.last_error = error
        task.locked_by = None
        task.lease_expires_at = None
        db.commit()
    finally:
        db.close()


async def execute_task(task_id: int, worker_id: str = WORKER_ID) -> None:
    db = SessionLocal()
    try:
        claimed = claim_task(db, task_id, worker_id)
    finally:
        db.close()
    if claimed:
        await execute_claimed_task(task_id, worker_id)


def schedule_task(task_id: int) -> None:
    asyncio.create_task(execute_task(task_id))

from __future__ import annotations

import typing
import asyncio

from celery import Celery

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.orchestration import archive_and_clean_logs, continue_after_wiring, expire_timed_out_runs, queued_run_ids, reclaim_expired_locks, start_run

celery_app = Celery("openslt", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(task_serializer="json", result_serializer="json", accept_content=["json"], timezone="UTC", enable_utc=True, beat_schedule={
    "reclaim-locks": {"task": "openslt.reclaim_expired_locks", "schedule": 60.0},
    "expire-runs": {"task": "openslt.expire_timed_out_runs", "schedule": 30.0},
    "dispatch-queue": {"task": "openslt.dispatch_queued_runs", "schedule": 5.0},
    "retention-cleanup": {"task": "openslt.archive_and_clean_logs", "schedule": 86400.0},
})


@celery_app.task(name="openslt.start_run")
def start_run_task(run_id: int) -> None: asyncio.run(start_run(run_id))


@celery_app.task(name="openslt.continue_run")
def continue_run_task(run_id: int) -> None: asyncio.run(continue_after_wiring(run_id))


@celery_app.task(name="openslt.reclaim_expired_locks")
def reclaim_expired_locks_task() -> int:
    db = SessionLocal()
    try: return reclaim_expired_locks(db)
    finally: db.close()


@celery_app.task(name="openslt.expire_timed_out_runs")
def expire_timed_out_runs_task() -> int:
    db = SessionLocal()
    try: return expire_timed_out_runs(db)
    finally: db.close()


@celery_app.task(name="openslt.dispatch_queued_runs")
def dispatch_queued_runs_task() -> int:
    db = SessionLocal()
    try: ids = queued_run_ids(db)
    finally: db.close()
    for run_id in ids: start_run_task.delay(run_id)
    return len(ids)


@celery_app.task(name="openslt.archive_and_clean_logs")
def archive_and_clean_logs_task() -> typing.Dict[str, int]:
    db = SessionLocal()
    try: return archive_and_clean_logs(db)
    finally: db.close()

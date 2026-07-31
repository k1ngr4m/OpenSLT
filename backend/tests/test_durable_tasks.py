from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import DurableTask, ResourceLock, TestRun as RunModel
from app.services import orchestration
from app.services.durable_tasks import (
    claim_task,
    enqueue_task,
    execute_task,
    recover_abandoned_tasks,
    renew_task_lease,
)
from conftest import create_plan_scenario, create_resource, publish_workflow


def test_enqueue_is_idempotent_and_claim_is_exclusive(client) -> None:
    with SessionLocal() as db:
        first = enqueue_task(db, "start_run", {"run_id": 42}, "start:42:v1")
        second = enqueue_task(db, "start_run", {"run_id": 42}, "start:42:v1")
        db.commit()
        assert first.id == second.id
        assert first.run_id == 42
        task_id = first.id

    with SessionLocal() as db:
        assert claim_task(db, task_id, "worker-a") is True
    with SessionLocal() as db:
        assert claim_task(db, task_id, "worker-b") is False
        task = db.get(DurableTask, task_id)
        assert task.status == "running"
        assert task.attempts == 1
        assert task.locked_by == "worker-a"


def test_recover_abandoned_tasks_uses_indexed_run_id(client) -> None:
    with SessionLocal() as db:
        run = RunModel(
            run_number="R20260731120000-QUEUE",
            plan_id=1,
            scenario_id=1,
            business_code="fut_mm",
            status="resource_queue",
            resource_ids=[],
            config_snapshot={},
            trace_id="queued-run",
            created_by=1,
        )
        db.add(run)
        db.flush()
        enqueue_task(
            db,
            "start_run",
            {"run_id": run.id},
            f"existing-start-run:{run.id}:v0",
        )
        db.commit()

        assert recover_abandoned_tasks(db) == 0
        tasks = list(
            db.scalars(
                select(DurableTask).where(
                    DurableTask.task_type == "start_run",
                    DurableTask.run_id == run.id,
                )
            ).all()
        )
        assert len(tasks) == 1


def test_expired_task_lease_is_recovered_and_reclaimed(client) -> None:
    with SessionLocal() as db:
        task = enqueue_task(db, "start_run", {"run_id": 42}, "recover:42")
        db.commit()
        task_id = task.id
        assert claim_task(db, task_id, "worker-a") is True
        task = db.get(DurableTask, task_id)
        task.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        assert recover_abandoned_tasks(db) == 1
        task = db.get(DurableTask, task_id)
        assert task.status == "queued"

    with SessionLocal() as db:
        assert claim_task(db, task_id, "worker-b") is True
        task = db.get(DurableTask, task_id)
        assert task.attempts == 2
        assert task.locked_by == "worker-b"


@pytest.mark.asyncio
async def test_task_execution_persists_success_and_failure(client, monkeypatch) -> None:
    calls = []

    async def successful_start(run_id: int) -> None:
        calls.append(run_id)

    monkeypatch.setattr(orchestration, "start_run", successful_start)
    with SessionLocal() as db:
        succeeded = enqueue_task(db, "start_run", {"run_id": 7}, "execute:7")
        db.commit()
        succeeded_id = succeeded.id

    await execute_task(succeeded_id, "test-worker")
    with SessionLocal() as db:
        succeeded = db.get(DurableTask, succeeded_id)
        assert succeeded.status == "succeeded"
        assert succeeded.attempts == 1
        assert calls == [7]

    async def failing_start(_run_id: int) -> None:
        raise RuntimeError("temporary failure")

    monkeypatch.setattr(orchestration, "start_run", failing_start)
    with SessionLocal() as db:
        failed = enqueue_task(
            db,
            "start_run",
            {"run_id": 8},
            "execute:8",
            max_attempts=1,
        )
        db.commit()
        failed_id = failed.id

    await execute_task(failed_id, "test-worker")
    with SessionLocal() as db:
        failed = db.get(DurableTask, failed_id)
        assert failed.status == "failed"
        assert failed.attempts == 1
        assert "temporary failure" in failed.last_error


def test_task_and_run_leases_can_be_renewed(client, admin_headers) -> None:
    resource = create_resource(client, admin_headers, "REM-lease")
    plan, scenario = create_plan_scenario(
        client, admin_headers, resource_ids=[resource["id"]]
    )
    publish_workflow(client, admin_headers, scenario, [resource["id"]], [
        {
            "node_key": "wiring",
            "node_type": "wiring_confirmation",
            "name": "确认接线",
            "config": {"diagram": "placeholder"},
        }
    ])
    run = client.post("/api/v1/runs", headers=admin_headers, json={
        "plan_id": plan["id"],
        "scenario_id": scenario["id"],
        "resource_ids": [resource["id"]],
    }).json()
    client.post(f"/api/v1/runs/{run['id']}/start", headers=admin_headers)

    with SessionLocal() as db:
        lock = db.query(ResourceLock).filter_by(run_id=run["id"]).one()
        lock.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=1)
        task = enqueue_task(db, "start_run", {"run_id": run["id"]}, "lease:test")
        db.commit()
        assert claim_task(db, task.id, "lease-worker", lease_seconds=10)
        task = db.get(DurableTask, task.id)
        db.refresh(task)
        original_task_lease = task.lease_expires_at
        assert renew_task_lease(db, task.id, "lease-worker", lease_seconds=60)
        db.refresh(task)
        assert task.lease_expires_at > original_task_lease
        assert orchestration.renew_run_locks(db, run["id"], lease_minutes=30) == 1
        db.commit()
        db.refresh(lock)
        assert lock.lease_expires_at.replace(tzinfo=None) > datetime.utcnow() + timedelta(minutes=20)

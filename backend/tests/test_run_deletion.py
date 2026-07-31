from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.time import beijing_now
from app.models import Artifact, AuditLog, DurableTask, LogRecord, TestRun as RunModel
from conftest import create_plan_scenario, create_resource, publish_workflow


def _create_run(client: TestClient, headers: dict[str, str]) -> dict:
    resource = create_resource(client, headers, "REM-delete")
    plan, scenario = create_plan_scenario(
        client,
        headers,
        resource_ids=[resource["id"]],
    )
    publish_workflow(
        client,
        headers,
        scenario,
        [resource["id"]],
        [
            {
                "node_key": "wiring",
                "node_type": "wiring_confirmation",
                "name": "确认接线",
                "config": {"diagram": "placeholder"},
            }
        ],
    )
    response = client.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "plan_id": plan["id"],
            "scenario_id": scenario["id"],
            "resource_ids": [resource["id"]],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_delete_run_removes_related_records_and_artifacts(
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    run = _create_run(client, admin_headers)
    artifact_directory = (
        settings.artifact_root
        / run["business_code"]
        / str(run["plan_id"])
        / str(run["scenario_id"])
        / run["run_number"]
    )
    artifact_directory.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_directory / "result.json"
    content = b'{"ok":true}'
    artifact_path.write_bytes(content)

    db = SessionLocal()
    try:
        db.add(
            Artifact(
                run_id=run["id"],
                artifact_type="parsed_data",
                name=artifact_path.name,
                path=str(artifact_path),
                content_type="application/json",
                size=len(content),
                checksum=hashlib.sha256(content).hexdigest(),
            )
        )
        db.add(
            LogRecord(
                log_type="run",
                level="INFO",
                event="run.test",
                message="待删除日志",
                trace_id=run["trace_id"],
                run_id=run["id"],
                source="test",
                detail={},
            )
        )
        db.add(
            DurableTask(
                task_type="start_run",
                payload={"run_id": run["id"]},
                run_id=run["id"],
                idempotency_key=f"delete-test:{run['id']}",
                status="succeeded",
                attempts=1,
                max_attempts=3,
                available_at=beijing_now(),
                finished_at=beijing_now(),
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.delete(f"/api/v1/runs/{run['id']}", headers=admin_headers)
    assert response.status_code == 204, response.text
    assert not artifact_directory.exists()

    db = SessionLocal()
    try:
        assert db.get(RunModel, run["id"]) is None
        assert db.scalar(select(Artifact).where(Artifact.run_id == run["id"])) is None
        assert db.scalar(select(LogRecord).where(LogRecord.run_id == run["id"])) is None
        assert db.scalar(select(DurableTask).where(DurableTask.run_id == run["id"])) is None
        audit = db.scalar(
            select(AuditLog).where(
                AuditLog.action == "run.delete",
                AuditLog.object_id == str(run["id"]),
            )
        )
        assert audit is not None
        assert audit.detail["run_number"] == run["run_number"]
        assert audit.detail["status"] == "draft"
    finally:
        db.close()


def test_delete_waiting_run_cancels_it_first(
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    run = _create_run(client, admin_headers)
    db = SessionLocal()
    try:
        record = db.get(RunModel, run["id"])
        assert record is not None
        record.status = "awaiting_step_retry"
        db.commit()
    finally:
        db.close()

    response = client.delete(f"/api/v1/runs/{run['id']}", headers=admin_headers)
    assert response.status_code == 204, response.text

    db = SessionLocal()
    try:
        audit = db.scalar(
            select(AuditLog).where(
                AuditLog.action == "run.delete",
                AuditLog.object_id == str(run["id"]),
            )
        )
        assert audit is not None
        assert audit.detail["status"] == "awaiting_step_retry"
    finally:
        db.close()


def test_delete_automatically_running_run_requires_cancel_first(
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    run = _create_run(client, admin_headers)
    db = SessionLocal()
    try:
        record = db.get(RunModel, run["id"])
        assert record is not None
        record.status = "running"
        db.commit()
    finally:
        db.close()

    response = client.delete(f"/api/v1/runs/{run['id']}", headers=admin_headers)
    assert response.status_code == 409
    assert response.json()["code"] == "RUN_DELETE_NOT_ALLOWED"

    db = SessionLocal()
    try:
        assert db.get(RunModel, run["id"]) is not None
    finally:
        db.close()

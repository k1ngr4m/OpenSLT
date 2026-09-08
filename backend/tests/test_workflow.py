from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.orm.exc import StaleDataError

from app.core.database import SessionLocal
from app.models import ConfigurationCaptureSnapshot, ResourceLock, RunStatusTransition, TestRun as RunRecord
from app.core.logging import redact
from app.services.orchestration import expire_timed_out_runs
from app.services.run_state import transition_run
from app.services import workflows
from conftest import create_plan_scenario, create_resource, publish_workflow


def node(key: str, node_type: str, name: str, config: dict) -> dict:
    return {"node_key": key, "node_type": node_type, "name": name, "config": config}


class FakeCaptureConnection:
    async def run(self, command, check=False):
        assert check is False
        if "ip -o -4 addr show" in command:
            return SimpleNamespace(exit_status=0, stdout="10.0.0.1/24\n", stderr="")
        if "lscpu" in command:
            return SimpleNamespace(exit_status=0, stdout="CPU(s): 32\n", stderr="")
        return SimpleNamespace(exit_status=0, stdout="captured\n", stderr="")

    def close(self):
        return None

    async def wait_closed(self):
        return None


def test_complete_dynamic_workflow(client, admin_headers, monkeypatch):
    async def fake_connect(**_options):
        return FakeCaptureConnection()

    monkeypatch.setattr(workflows.asyncssh, "connect", fake_connect)
    resource = create_resource(client, admin_headers, "REM-01")
    plan, scenario = create_plan_scenario(client, admin_headers, resource_ids=[resource["id"]])
    publish_workflow(client, admin_headers, scenario, [resource["id"]], [
        node("server", "server_config", "采集 REM 配置", {"targets": [{"resource_type": "rem", "fields": ["ip", "cpu_model"]}]}),
        node("wiring", "wiring_confirmation", "确认接线", {"diagram": "placeholder"}),
    ])
    created = client.post("/api/v1/runs", headers=admin_headers, json={"plan_id": plan["id"], "scenario_id": scenario["id"], "resource_ids": [resource["id"]]})
    assert created.status_code == 201, created.text
    assert created.json()["timeout_at"] is None
    run_id = created.json()["id"]
    assert [item["node_type"] for item in created.json()["steps"]] == ["server_config", "wiring_confirmation"]
    assert client.post(f"/api/v1/runs/{run_id}/start", headers=admin_headers).status_code == 200
    ready = client.get(f"/api/v1/runs/{run_id}", headers=admin_headers).json()
    assert ready["status"] == "awaiting_step_completion"
    assert [item["status"] for item in ready["steps"]] == ["succeeded", "waiting"]
    first_step = ready["steps"][0]
    executed = ready
    assert executed["steps"][0]["result_summary"]["failed"] == 0
    captures = client.get(
        f"/api/v1/runs/{run_id}/steps/{first_step['id']}/capture-snapshots",
        headers=admin_headers,
    )
    assert captures.status_code == 200, captures.text
    capture_items = captures.json()[0]["items"]
    assert [item["item_key"] for item in capture_items] == ["ip", "cpu_model"]
    assert capture_items[0]["value_text"] == "10.0.0.1/24"
    assert capture_items[1]["value_text"] == "CPU(s): 32"
    empty_captures = client.get(
        f"/api/v1/runs/{run_id}/steps/{executed['steps'][1]['id']}/capture-snapshots",
        headers=admin_headers,
    )
    assert empty_captures.status_code == 200
    assert empty_captures.json() == []
    second_step = executed["steps"][1]
    confirmed = client.post(
        f"/api/v1/runs/{run_id}/steps/{second_step['id']}/complete",
        headers=admin_headers,
    )
    assert confirmed.status_code == 200
    completed = client.get(f"/api/v1/runs/{run_id}", headers=admin_headers).json()
    assert completed["status"] == "completed"
    assert completed["progress"] == 100
    with SessionLocal() as db:
        snapshots = db.query(ConfigurationCaptureSnapshot).filter(ConfigurationCaptureSnapshot.run_id == run_id).all()
        assert len(snapshots) == 1
        assert snapshots[0].status == "succeeded"
        assert all(lock.released_at is not None for lock in db.query(ResourceLock).filter(ResourceLock.run_id == run_id))


def test_run_timeout_marks_run_and_steps_and_releases_locks(client, admin_headers):
    resource = create_resource(client, admin_headers, "REM-no-timeout")
    plan, scenario = create_plan_scenario(client, admin_headers, resource_ids=[resource["id"]])
    publish_workflow(client, admin_headers, scenario, [resource["id"]], [
        node("wiring", "wiring_confirmation", "确认接线", {"diagram": "placeholder"}),
    ])
    created = client.post("/api/v1/runs", headers=admin_headers, json={
        "plan_id": plan["id"],
        "scenario_id": scenario["id"],
        "resource_ids": [resource["id"]],
        "timeout_minutes": 30,
    })
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    assert created.json()["timeout_at"] is not None
    client.post(f"/api/v1/runs/{run_id}/start", headers=admin_headers)

    with SessionLocal() as db:
        run = db.get(RunRecord, run_id)
        assert run is not None
        run.timeout_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
        assert expire_timed_out_runs(db) == 1

    timed_out = client.get(f"/api/v1/runs/{run_id}", headers=admin_headers).json()
    assert timed_out["status"] == "timed_out"
    assert timed_out["error_code"] == "RUN_TIMED_OUT"
    assert timed_out["steps"][0]["status"] == "cancelled"
    assert timed_out["status_transitions"][-1]["source"] == "scheduler"
    with SessionLocal() as db:
        assert all(
            lock.released_at is not None
            for lock in db.query(ResourceLock).filter(ResourceLock.run_id == run_id)
        )


def test_run_status_version_rejects_stale_concurrent_transition(client, admin_headers):
    resource = create_resource(client, admin_headers, "REM-versioned")
    plan, scenario = create_plan_scenario(
        client, admin_headers, resource_ids=[resource["id"]]
    )
    publish_workflow(client, admin_headers, scenario, [resource["id"]], [
        node("wiring", "wiring_confirmation", "确认接线", {"diagram": "placeholder"}),
    ])
    created = client.post("/api/v1/runs", headers=admin_headers, json={
        "plan_id": plan["id"],
        "scenario_id": scenario["id"],
        "resource_ids": [resource["id"]],
    }).json()

    first = SessionLocal()
    second = SessionLocal()
    try:
        first_run = first.get(RunRecord, created["id"])
        second_run = second.get(RunRecord, created["id"])
        transition_run(first_run, "resource_queue", source="test-first")
        transition_run(second_run, "resource_queue", source="test-second")
        first.commit()
        with pytest.raises(StaleDataError):
            second.commit()
        second.rollback()
    finally:
        first.close()
        second.close()

    with SessionLocal() as db:
        run = db.get(RunRecord, created["id"])
        transitions = db.query(RunStatusTransition).filter_by(run_id=run.id).all()
        assert run.status == "resource_queue"
        assert run.status_version == 1
        assert [item.source for item in transitions] == ["test-first"]


def test_resource_lock_queues_competing_run(client, admin_headers):
    resource = create_resource(client, admin_headers, "REM-shared")
    plan, scenario = create_plan_scenario(client, admin_headers, resource_ids=[resource["id"]])
    publish_workflow(client, admin_headers, scenario, [resource["id"]], [
        node("wiring", "wiring_confirmation", "确认接线", {"diagram": "placeholder"}),
    ])
    payload = {"plan_id": plan["id"], "scenario_id": scenario["id"], "resource_ids": [resource["id"]]}
    first = client.post("/api/v1/runs", headers=admin_headers, json=payload).json()
    second = client.post("/api/v1/runs", headers=admin_headers, json=payload).json()
    client.post(f"/api/v1/runs/{first['id']}/start", headers=admin_headers)
    client.post(f"/api/v1/runs/{second['id']}/start", headers=admin_headers)
    queued = client.get(f"/api/v1/runs/{second['id']}", headers=admin_headers).json()
    assert queued["status"] == "resource_queue"
    assert "资源被占用" in queued["queue_reason"]
    client.post(f"/api/v1/runs/{first['id']}/cancel", headers=admin_headers)
    client.post(f"/api/v1/runs/{second['id']}/start", headers=admin_headers)
    assert client.get(f"/api/v1/runs/{second['id']}", headers=admin_headers).json()["status"] == "awaiting_step_completion"


def test_sensitive_data_redaction():
    value = "password=hunter2 token:abc Bearer ey.secret.value -----BEGIN PRIVATE KEY-----raw-----END PRIVATE KEY-----"
    redacted = redact(value)
    assert "hunter2" not in redacted
    assert "abc" not in redacted
    assert "ey.secret.value" not in redacted
    assert "PRIVATE KEY-----raw" not in redacted
    assert redacted.count("[REDACTED]") >= 4


def test_capture_chain_continues_after_manual_confirmation(client, admin_headers, monkeypatch):
    async def fake_connect(**_options):
        return FakeCaptureConnection()

    monkeypatch.setattr(workflows.asyncssh, "connect", fake_connect)
    resource = create_resource(client, admin_headers, "REM-continuous")
    plan, scenario = create_plan_scenario(client, admin_headers, resource_ids=[resource["id"]])
    capture = {"targets": [{"resource_type": "rem", "fields": ["cpu_model"]}]}
    publish_workflow(client, admin_headers, scenario, [resource["id"]], [
        node("first", "server_config", "采集一", capture),
        node("second", "server_config", "采集二", capture),
        node("wiring", "wiring_confirmation", "确认接线", {"diagram": "placeholder"}),
        node("last", "server_config", "确认后采集", capture),
    ])
    created = client.post("/api/v1/runs", headers=admin_headers, json={
        "plan_id": plan["id"], "scenario_id": scenario["id"], "resource_ids": [resource["id"]],
    }).json()
    run_id = created["id"]
    assert client.post(f"/api/v1/runs/{run_id}/start", headers=admin_headers).status_code == 200
    waiting = client.get(f"/api/v1/runs/{run_id}", headers=admin_headers).json()
    assert [step["status"] for step in waiting["steps"]] == ["succeeded", "succeeded", "waiting", "pending"]
    step_id = waiting["steps"][2]["id"]
    assert client.post(f"/api/v1/runs/{run_id}/steps/{step_id}/confirm", headers=admin_headers).status_code == 200
    completed = client.get(f"/api/v1/runs/{run_id}", headers=admin_headers).json()
    assert completed["status"] == "completed"
    assert all(step["status"] == "succeeded" for step in completed["steps"])

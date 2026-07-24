from __future__ import annotations

from app.core.database import SessionLocal
from app.models import ConfigurationCaptureSnapshot, ResourceLock
from app.core.logging import redact
from conftest import create_plan_scenario, create_resource, publish_workflow


def node(key: str, node_type: str, name: str, config: dict) -> dict:
    return {"node_key": key, "node_type": node_type, "name": name, "config": config}


def test_complete_dynamic_workflow(client, admin_headers):
    resource = create_resource(client, admin_headers, "REM-01")
    plan, scenario = create_plan_scenario(client, admin_headers, resource_ids=[resource["id"]])
    publish_workflow(client, admin_headers, scenario, [resource["id"]], [
        node("server", "server_config", "采集 REM 配置", {"targets": [{"resource_type": "rem", "fields": ["ip", "cpu_model"]}]}),
        node("wiring", "wiring_confirmation", "确认接线", {"diagram": "placeholder"}),
    ])
    created = client.post("/api/v1/runs", headers=admin_headers, json={"plan_id": plan["id"], "scenario_id": scenario["id"], "resource_ids": [resource["id"]], "timeout_minutes": 30})
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]
    assert [item["node_type"] for item in created.json()["steps"]] == ["server_config", "wiring_confirmation"]
    assert client.post(f"/api/v1/runs/{run_id}/start", headers=admin_headers).status_code == 200
    ready = client.get(f"/api/v1/runs/{run_id}", headers=admin_headers).json()
    assert ready["status"] == "awaiting_step_start"
    assert [item["status"] for item in ready["steps"]] == ["pending", "pending"]

    first_step = ready["steps"][0]
    assert client.post(
        f"/api/v1/runs/{run_id}/steps/{first_step['id']}/start",
        headers=admin_headers,
    ).status_code == 200
    executed = client.get(f"/api/v1/runs/{run_id}", headers=admin_headers).json()
    assert executed["status"] == "awaiting_step_completion"
    assert executed["steps"][0]["status"] == "waiting"
    assert executed["steps"][0]["result_summary"]["failed"] == 0
    assert executed["steps"][1]["status"] == "pending"
    assert client.post(
        f"/api/v1/runs/{run_id}/steps/{first_step['id']}/complete",
        headers=admin_headers,
    ).status_code == 200

    second_ready = client.get(f"/api/v1/runs/{run_id}", headers=admin_headers).json()
    assert second_ready["status"] == "awaiting_step_start"
    second_step = second_ready["steps"][1]
    assert client.post(
        f"/api/v1/runs/{run_id}/steps/{second_step['id']}/start",
        headers=admin_headers,
    ).status_code == 200
    assert client.get(f"/api/v1/runs/{run_id}", headers=admin_headers).json()["status"] == "awaiting_step_completion"
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


def test_resource_lock_queues_competing_run(client, admin_headers):
    resource = create_resource(client, admin_headers, "REM-shared")
    plan, scenario = create_plan_scenario(client, admin_headers, resource_ids=[resource["id"]])
    publish_workflow(client, admin_headers, scenario, [resource["id"]], [
        node("wiring", "wiring_confirmation", "确认接线", {"diagram": "placeholder"}),
    ])
    payload = {"plan_id": plan["id"], "scenario_id": scenario["id"], "resource_ids": [resource["id"]], "timeout_minutes": 30}
    first = client.post("/api/v1/runs", headers=admin_headers, json=payload).json()
    second = client.post("/api/v1/runs", headers=admin_headers, json=payload).json()
    client.post(f"/api/v1/runs/{first['id']}/start", headers=admin_headers)
    client.post(f"/api/v1/runs/{second['id']}/start", headers=admin_headers)
    queued = client.get(f"/api/v1/runs/{second['id']}", headers=admin_headers).json()
    assert queued["status"] == "resource_queue"
    assert "资源被占用" in queued["queue_reason"]
    client.post(f"/api/v1/runs/{first['id']}/cancel", headers=admin_headers)
    client.post(f"/api/v1/runs/{second['id']}/start", headers=admin_headers)
    assert client.get(f"/api/v1/runs/{second['id']}", headers=admin_headers).json()["status"] == "awaiting_step_start"


def test_sensitive_data_redaction():
    value = "password=hunter2 token:abc Bearer ey.secret.value -----BEGIN PRIVATE KEY-----raw-----END PRIVATE KEY-----"
    redacted = redact(value)
    assert "hunter2" not in redacted
    assert "abc" not in redacted
    assert "ey.secret.value" not in redacted
    assert "PRIVATE KEY-----raw" not in redacted
    assert redacted.count("[REDACTED]") >= 4

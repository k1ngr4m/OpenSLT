from app.core.database import SessionLocal
from app.models import ResourceLock
from app.core.logging import redact
from conftest import create_plan_scenario, create_resource


def test_complete_run_and_reports(client, admin_headers):
    resource = create_resource(client, admin_headers, "REM-01")
    plan, scenario = create_plan_scenario(client, admin_headers)
    created = client.post("/api/v1/runs", headers=admin_headers, json={"plan_id": plan["id"], "scenario_id": scenario["id"], "resource_ids": [resource["id"]], "timeout_minutes": 30})
    assert created.status_code == 201
    run_id = created.json()["id"]
    started = client.post(f"/api/v1/runs/{run_id}/start", headers=admin_headers)
    assert started.status_code == 200
    assert client.get(f"/api/v1/runs/{run_id}", headers=admin_headers).json()["status"] == "awaiting_wiring"
    confirmed = client.post(f"/api/v1/runs/{run_id}/confirm-wiring", headers=admin_headers)
    assert confirmed.status_code == 200
    ready = client.get(f"/api/v1/runs/{run_id}", headers=admin_headers).json()
    assert ready["status"] == "awaiting_review"
    assert len(ready["metrics"]) == 7
    assert ready["verdict"] is None
    assert all("rule_result" not in metric for metric in ready["metrics"])
    assert not ({"parameters", "actions", "statistics_rules"} & ready["config_snapshot"]["scenario"].keys())
    verdict = client.post(f"/api/v1/runs/{run_id}/verdict", headers=admin_headers, json={"final_result": "passed", "issue_description": "", "notes": "验收通过"})
    assert verdict.status_code == 200
    assert "automatic_result" not in verdict.json()
    completed = client.get(f"/api/v1/runs/{run_id}", headers=admin_headers).json()
    assert completed["status"] == "completed"
    assert {item["artifact_type"] for item in completed["artifacts"]} >= {"web_report", "excel_report", "pdf_report", "parsed_data"}
    with SessionLocal() as db:
        assert all(lock.released_at is not None for lock in db.query(ResourceLock).filter(ResourceLock.run_id == run_id))


def test_resource_lock_queues_competing_run(client, admin_headers):
    resource = create_resource(client, admin_headers, "REM-shared")
    plan, scenario = create_plan_scenario(client, admin_headers)
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
    assert client.get(f"/api/v1/runs/{second['id']}", headers=admin_headers).json()["status"] == "awaiting_wiring"


def test_sensitive_data_redaction():
    value = "password=hunter2 token:abc Bearer ey.secret.value -----BEGIN PRIVATE KEY-----raw-----END PRIVATE KEY-----"
    redacted = redact(value)
    assert "hunter2" not in redacted
    assert "abc" not in redacted
    assert "ey.secret.value" not in redacted
    assert "PRIVATE KEY-----raw" not in redacted
    assert redacted.count("[REDACTED]") >= 4

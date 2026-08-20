from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.core.time import beijing_now
from app.models import Metric, RunComparison, TestRun as RunModel, Verdict
from conftest import create_plan_scenario, create_resource, publish_workflow


def _create_completed_run(
    client: TestClient,
    headers: dict[str, str],
    plan: dict,
    scenario: dict,
    resource_id: int,
    *,
    value: float,
    passed: bool,
    script_checksum: str = "a" * 64,
) -> dict:
    response = client.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "plan_id": plan["id"],
            "scenario_id": scenario["id"],
            "resource_ids": [resource_id],
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    with SessionLocal() as db:
        run = db.get(RunModel, payload["id"])
        step = run.steps[0]
        step.code = "statistics"
        step.name = "数据统计"
        step.node_type = "data_statistics"
        step.result_summary = {
            "statistics_latest_success_analysis_no": 1,
            "statistics_analyses": [{
                "analysis_no": 1,
                "status": "succeeded",
                "artifact_id": 9000 + run.id,
                "artifact_checksum": "b" * 64,
                "script": {"filename": "statistics_order.py", "checksum": script_checksum},
                "max_latency_ns": 999999999,
            }],
        }
        run.status = "completed"
        run.progress = 100
        run.finished_at = beijing_now() - timedelta(minutes=run.id)
        db.add(Metric(
            run_id=run.id,
            name="数据统计/latency.csv/平均值",
            value=value,
            unit="ns",
            sample_count=100,
            detail={
                "statistics_step_id": step.id,
                "source_file": "latency.csv",
                "source_path": "/srv/statistics/latency.csv",
                "metric_key": "average",
                "metric_label": "平均值",
                "script_filename": "statistics_order.py",
                "script_checksum": script_checksum,
                "max_latency_ns": 999999999,
            },
        ))
        if passed:
            db.add(Verdict(
                run_id=run.id,
                final_result="passed",
                issue_description="",
                notes="",
                reviewed_by=1,
                reviewed_at=beijing_now(),
            ))
        db.commit()
    return payload


def _comparison_runs(client: TestClient, headers: dict[str, str]) -> tuple[dict, dict]:
    resource = create_resource(client, headers, "REM-comparison")
    plan, scenario = create_plan_scenario(client, headers, resource_ids=[resource["id"]])
    publish_workflow(
        client,
        headers,
        scenario,
        [resource["id"]],
        [{
            "node_key": "wiring",
            "node_type": "wiring_confirmation",
            "name": "确认接线",
            "config": {"diagram": "placeholder"},
        }],
    )
    baseline = _create_completed_run(
        client, headers, plan, scenario, resource["id"], value=100.0, passed=True,
    )
    target = _create_completed_run(
        client, headers, plan, scenario, resource["id"], value=125.0, passed=False,
    )
    return baseline, target


def test_run_comparison_recommends_passed_baseline_and_pins_metric_snapshot(
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    baseline, target = _comparison_runs(client, admin_headers)

    candidates = client.get(
        f"/api/v1/runs/{target['id']}/comparison-candidates",
        headers=admin_headers,
    )
    assert candidates.status_code == 200, candidates.text
    assert candidates.json()[0] == {
        "run_id": baseline["id"],
        "run_number": baseline["run_number"],
        "finished_at": candidates.json()[0]["finished_at"],
        "verdict": "passed",
        "workflow_version_id": baseline["workflow_version_id"],
        "compatible": True,
        "warnings": [],
        "matched_metric_count": 1,
        "metric_count": 1,
        "recommended": True,
    }

    saved = client.put(
        f"/api/v1/runs/{target['id']}/comparison",
        headers=admin_headers,
        json={"baseline_run_id": baseline["id"]},
    )
    assert saved.status_code == 200, saved.text
    payload = saved.json()
    assert payload["compatible"] is True
    assert payload["target_analysis_refs"][0]["analysis_no"] == 1
    assert payload["baseline_analysis_refs"][0]["artifact_checksum"] == "b" * 64
    assert payload["rows"][0]["assessment"] == "regressed"
    assert payload["rows"][0]["absolute_delta"] == 25.0
    assert payload["rows"][0]["percentage_delta"] == 25.0

    with SessionLocal() as db:
        metric = db.query(Metric).filter(Metric.run_id == baseline["id"]).one()
        metric.value = 50.0
        db.commit()

    loaded = client.get(
        f"/api/v1/runs/{target['id']}/comparison",
        headers=admin_headers,
    )
    assert loaded.status_code == 200
    assert loaded.json()["baseline_metrics_changed"] is True
    assert loaded.json()["rows"][0]["baseline_value"] == 100.0
    assert loaded.json()["rows"][0]["absolute_delta"] == 25.0


def test_run_comparison_rejects_cross_scenario_and_survives_baseline_deletion(
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    baseline, target = _comparison_runs(client, admin_headers)
    saved = client.put(
        f"/api/v1/runs/{target['id']}/comparison",
        headers=admin_headers,
        json={"baseline_run_id": baseline["id"]},
    )
    assert saved.status_code == 200

    deleted = client.delete(f"/api/v1/runs/{baseline['id']}", headers=admin_headers)
    assert deleted.status_code == 204, deleted.text
    loaded = client.get(
        f"/api/v1/runs/{target['id']}/comparison",
        headers=admin_headers,
    )
    assert loaded.status_code == 200
    assert loaded.json()["baseline_run_id"] is None
    assert loaded.json()["baseline_run_number"] == baseline["run_number"]
    assert loaded.json()["rows"][0]["baseline_value"] == 100.0

    with SessionLocal() as db:
        comparison = db.query(RunComparison).filter(RunComparison.run_id == target["id"]).one()
        assert comparison.baseline_run_id is None


def test_run_comparison_marks_script_mismatch_incompatible(
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    baseline, target = _comparison_runs(client, admin_headers)
    with SessionLocal() as db:
        metric = db.query(Metric).filter(Metric.run_id == baseline["id"]).one()
        metric.detail = {**metric.detail, "script_checksum": "c" * 64}
        db.commit()

    response = client.put(
        f"/api/v1/runs/{target['id']}/comparison",
        headers=admin_headers,
        json={"baseline_run_id": baseline["id"]},
    )
    assert response.status_code == 200
    assert response.json()["compatible"] is False
    assert response.json()["rows"][0]["assessment"] == "incompatible"
    assert "统计脚本 checksum 不一致" in response.json()["warnings"]

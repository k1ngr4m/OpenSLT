from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas import WorkflowNodeWrite
from app.services.workflow_handlers.registry import registry
from conftest import create_plan_scenario


def test_report_generation_node_schema_and_handler_registration():
    assert "report_generation" in registry.node_types
    adapter = TypeAdapter(WorkflowNodeWrite)
    parsed = adapter.validate_python({
        "node_key": "report", "node_type": "report_generation",
        "name": "生成报告", "config": {},
    })
    assert parsed.config.model_dump() == {}
    with pytest.raises(ValidationError):
        adapter.validate_python({
            "node_key": "report", "node_type": "report_generation",
            "name": "生成报告", "config": {"format": "pdf"},
        })


def test_report_generation_structure_constraints(client, admin_headers):
    parser_response = client.post("/api/v1/resources", headers=admin_headers, json={
        "name": "Parser-report", "resource_type": "parser", "business_code": "fut_mm",
        "host": "127.0.0.1", "ssh_port": 22, "username": "tester",
        "auth_type": "password", "password": "secret", "remote_path": "/tmp/parser",
        "capabilities": {"parser_tool": "soft_cffex_speed_analysis_v2"},
        "version_info": "test", "notes": "", "is_enabled": True,
    })
    assert parser_response.status_code == 201, parser_response.text
    parser = parser_response.json()
    _, scenario = create_plan_scenario(
        client, admin_headers, required_types=["parser"], resource_ids=[parser["id"]]
    )
    endpoint = f"/api/v1/scenarios/{scenario['id']}/workflow"
    revision = client.get(endpoint, headers=admin_headers).json()["draft"]["revision"]
    statistics = {
        "node_key": "statistics", "node_type": "data_statistics", "name": "统计",
        "config": {
            "parser_node_key": "", "script_filename": "statistics.py",
            "script_checksum": "a" * 64, "max_latency_ns": 999999999,
        },
    }
    report = {
        "node_key": "report", "node_type": "report_generation",
        "name": "生成报告", "config": {},
    }

    def save(nodes):
        nonlocal revision
        response = client.put(endpoint, headers=admin_headers, json={
            "expected_revision": revision,
            "resource_ids": [parser["id"]],
            "nodes": nodes,
        })
        assert response.status_code == 200, response.text
        revision = response.json()["draft"]["revision"]
        return {item["message"] for item in response.json()["validation_errors"]}

    assert "报告生成节点前至少需要一个数据统计节点" in save([report])
    assert "报告生成节点必须位于工作流末尾" in save([
        statistics, report, {**statistics, "node_key": "statistics-2"},
    ])
    assert "每个工作流最多只能有一个报告生成节点" in save([
        statistics, report, {**report, "node_key": "report-2"},
    ])
    report_messages = {message for message in save([statistics, report]) if "报告生成节点" in message}
    assert report_messages == set()


@pytest.mark.asyncio
async def test_statistics_completion_waits_for_review_and_generates_report_once(
    client, admin_headers, tmp_path, monkeypatch
):
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models import ResourceLock, RunStep, TestScenario as ScenarioModel, TestRun as RunModel
    from app.services.orchestration import complete_workflow_step
    from test_statistics_workflow import create_statistics_run

    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
    plan, scenario = create_plan_scenario(client, admin_headers)
    with SessionLocal() as db:
        run, _parser, step, _artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        run.workflow_version_id = db.get(ScenarioModel, scenario["id"]).draft_workflow_version_id
        run.config_snapshot = {"plan": plan, "scenario": scenario}
        run.status = "awaiting_step_completion"
        step.status = "waiting"
        step.result_summary = {"statistics_results": [{"source_file": "latency.csv", "metrics": []}]}
        report = RunStep(code="report", node_type="report_generation", name="生成报告", position=3, status="pending", config_snapshot={})
        run.steps.append(report)
        db.flush()
        await complete_workflow_step(db, run, step.id, actor_id=1)
        assert run.status == "awaiting_review"
        assert report.status == "waiting"
        assert not [item for item in run.artifacts if item.artifact_type.endswith("report")]
        assert not db.query(ResourceLock).filter_by(run_id=run.id, released_at=None).count()
        db.commit()
        run_id = run.id
    response = client.post(f"/api/v1/runs/{run_id}/verdict", headers=admin_headers, json={"final_result": "passed", "issue_description": "", "notes": "checked"})
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        run = db.get(RunModel, run_id)
        assert run.status == "completed"
        report = next(item for item in run.steps if item.node_type == "report_generation")
        assert report.status == "succeeded"
        assert report.result_summary["report_version"] == 1
        reports = [item for item in run.artifacts if item.artifact_type.endswith("report")]
        assert len(reports) == 3
        html = next(item for item in reports if item.artifact_type == "web_report")
        assert "checked" in open(html.path, encoding="utf-8").read()

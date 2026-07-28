from __future__ import annotations

import hashlib
import json
import shlex
import typing
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.database import SessionLocal
from app.models import Artifact, Metric, Resource, RunStep, TestRun as RunModel
from app.services import statistics_execution
from app.services.statistics_execution import (
    StatisticsScriptOutput,
    execute_statistics_node,
    select_statistics_inputs,
)
from app.services.statistics_scripts import validate_filename
from app.services.workflow_core import WorkflowError
from conftest import create_plan_scenario


def create_parser_resource(client, headers):
    response = client.post("/api/v1/resources", headers=headers, json={
        "name": "Parser-Statistics", "resource_type": "parser", "business_code": "fut_mm",
        "host": "127.0.0.1", "ssh_port": 22, "username": "tester",
        "auth_type": "password", "password": "secret", "remote_path": "/tmp/parser",
        "capabilities": {"parser_tool": "soft_cffex_speed_analysis_v2"},
        "version_info": "test", "notes": "", "is_enabled": True,
    })
    assert response.status_code == 201, response.text
    return response.json()


def create_statistics_run(
    db,
    plan_id,
    scenario_id,
    tmp_path: Path,
    run_number: str = "R-STATISTICS-1",
):
    run = RunModel(
        run_number=run_number, plan_id=plan_id, scenario_id=scenario_id,
        business_code="fut_mm", status="awaiting_step_start", progress=50,
        config_snapshot={}, trace_id="statistics-test", created_by=1,
    )
    parser = RunStep(
        code="parse", name="Parse", node_type="parser_parse", position=1,
        status="succeeded", progress=100, config_snapshot={}, result_summary={},
    )
    statistics = RunStep(
        code="statistics", name="Statistics", node_type="data_statistics", position=2,
        status="pending", progress=0,
        config_snapshot={
            "parser_node_key": "parse",
            "script_filename": "statistics_cffex.py",
            "script_checksum": "a" * 64,
            "max_latency_ns": 999999999,
        },
        result_summary={},
    )
    run.steps = [parser, statistics]
    db.add(run)
    db.flush()
    csv_path = tmp_path / "rem_client_new_to_market_speed.csv"
    csv_path.write_text("header\n1\n", encoding="utf-8")
    artifact = Artifact(
        run_id=run.id, step_id=parser.id, artifact_type="parsed_csv", name=csv_path.name,
        path=str(csv_path), content_type="text/csv", size=csv_path.stat().st_size,
        checksum=hashlib.sha256(csv_path.read_bytes()).hexdigest(), is_immutable=True,
    )
    db.add(artifact)
    db.flush()
    return run, parser, statistics, artifact


class FakeSFTP:
    async def makedirs(self, _path, exist_ok=False):
        return None

    async def put(self, _local, _remote):
        return None

    async def posix_rename(self, _source, _target):
        return None

    async def remove(self, _path):
        return None

    def exit(self):
        return None


class FakeConnection:
    def __init__(self, handler):
        self.handler = handler
        self.sftp = FakeSFTP()

    async def start_sftp_client(self):
        return self.sftp

    async def run(self, command, check=False, timeout=None):
        return self.handler(command)

    def close(self):
        return None

    async def wait_closed(self):
        return None


def install_statistics_fakes(monkeypatch, handler, checksum: str = "a" * 64) -> None:
    async def fake_connect(**_kwargs):
        return FakeConnection(handler)

    async def fake_read(_resource, filename):
        return {
            "name": filename, "checksum": checksum, "executable": True,
            "path": f"/tmp/parser/{filename}",
        }

    monkeypatch.setattr(statistics_execution.asyncssh, "connect", fake_connect)
    monkeypatch.setattr(statistics_execution.statistics_script_service, "read", fake_read)


def statistics_payload(filename: str, value: float = 1234.5) -> dict[str, typing.Any]:
    return {
        "schema_version": 1,
        "source_file": filename,
        "unit": "ns",
        "sample_count": 10,
        "excluded_counts": {"above_limit": 1, "negative": 2, "invalid": 0},
        "metrics": [
            {"key": "average", "label": "平均值", "value": value},
            {"key": "p99", "label": "99%", "value": value + 100},
        ],
    }


def add_parsed_csv_artifact(db, run, parser, tmp_path: Path, filename: str) -> Artifact:
    csv_path = tmp_path / filename
    csv_path.write_text("header\n1\n", encoding="utf-8")
    artifact = Artifact(
        run_id=run.id, step_id=parser.id, artifact_type="parsed_csv", name=csv_path.name,
        path=str(csv_path), content_type="text/csv", size=csv_path.stat().st_size,
        checksum=hashlib.sha256(csv_path.read_bytes()).hexdigest(), is_immutable=True,
    )
    db.add(artifact)
    db.flush()
    return artifact


def response_error_code(response) -> str:
    payload = response.json()
    detail = payload.get("detail")
    if isinstance(detail, dict):
        return detail["code"]
    return payload["code"]


def test_statistics_output_contract_and_script_name_validation():
    output = StatisticsScriptOutput.model_validate({
        "schema_version": 1,
        "source_file": "latency.csv",
        "unit": "ns",
        "sample_count": 3,
        "excluded_counts": {"above_limit": 1, "negative": 0, "invalid": 0},
        "metrics": [{"key": "average", "label": "平均值", "value": 12.5}],
    })
    assert output.metrics[0].value == 12.5
    with pytest.raises(ValidationError):
        StatisticsScriptOutput.model_validate({
            **output.model_dump(),
            "unit": "us",
        })
    with pytest.raises(ValidationError):
        StatisticsScriptOutput.model_validate({
            **output.model_dump(),
            "metrics": [
                {"key": "average", "label": "A", "value": 1},
                {"key": "average", "label": "B", "value": 2},
            ],
        })
    assert validate_filename("statistics_cffex.py") == "statistics_cffex.py"
    with pytest.raises(Exception):
        validate_filename("../statistics.py")


def test_statistics_input_selection_rejects_other_artifacts(client, admin_headers, tmp_path):
    plan, scenario = create_plan_scenario(client, admin_headers)
    with SessionLocal() as db:
        run, _parser, step, artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        selection = select_statistics_inputs(db, run, step, [artifact.id], actor_id=1)
        assert selection["inputs"][0]["filename"] == artifact.name
        other = Artifact(
            run_id=run.id, step_id=step.id, artifact_type="parsed_csv", name="wrong.csv",
            path=artifact.path, content_type="text/csv", size=artifact.size,
            checksum=artifact.checksum, is_immutable=True,
        )
        db.add(other)
        db.flush()
        with pytest.raises(WorkflowError) as changed:
            select_statistics_inputs(db, run, step, [other.id], actor_id=1)
        assert changed.value.code == "STATISTICS_INPUT_INVALID"


def test_statistics_inputs_endpoint_rejects_wrong_state_and_foreign_artifact(
    client, admin_headers, tmp_path
):
    plan, scenario = create_plan_scenario(client, admin_headers)
    with SessionLocal() as db:
        run, _parser, step, artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        _other_run, _other_parser, _other_step, other_artifact = create_statistics_run(
            db, plan["id"], scenario["id"], tmp_path, "R-STATISTICS-2"
        )
        db.commit()
        run_id = run.id
        step_id = step.id
        artifact_id = artifact.id
        other_artifact_id = other_artifact.id

    response = client.put(
        f"/api/v1/runs/{run_id}/steps/{step_id}/statistics-inputs",
        headers=admin_headers,
        json={"artifact_ids": [other_artifact_id]},
    )
    assert response.status_code == 409
    assert response_error_code(response) == "STATISTICS_INPUT_INVALID"

    with SessionLocal() as db:
        stored = db.get(RunModel, run_id)
        stored.status = "running"
        db.commit()

    response = client.put(
        f"/api/v1/runs/{run_id}/steps/{step_id}/statistics-inputs",
        headers=admin_headers,
        json={"artifact_ids": [artifact_id]},
    )
    assert response.status_code == 409
    assert response_error_code(response) == "STATISTICS_SELECTION_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_statistics_execution_creates_metrics_and_json_artifact(
    client, admin_headers, tmp_path, monkeypatch
):
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)

    def handler(command):
        parts = shlex.split(command)
        payload = statistics_payload(Path(parts[1]).name, 1234.5)
        return SimpleNamespace(exit_status=0, stdout=json.dumps(payload), stderr="")

    install_statistics_fakes(monkeypatch, handler)

    with SessionLocal() as db:
        run, _parser, step, artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        resource = db.get(Resource, parser_data["id"])
        select_statistics_inputs(db, run, step, [artifact.id], actor_id=1)
        result = await execute_statistics_node(
            db,
            run,
            step,
            SimpleNamespace(node_type="data_statistics", config=step.config_snapshot),
            {"parser": resource},
        )
        assert result["statistics_results"][0]["sample_count"] == 10
        metrics = list(db.query(Metric).filter(Metric.run_id == run.id).all())
        assert {item.detail["metric_key"] for item in metrics} == {"average", "p99"}
        result_artifact = db.get(Artifact, result["statistics_artifact_id"])
        assert result_artifact.artifact_type == "statistics_result_json"
        assert Path(result_artifact.path).is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected_code"),
    [
        (SimpleNamespace(exit_status=1, stdout="", stderr="boom"), "STATISTICS_SCRIPT_FAILED"),
        (SimpleNamespace(exit_status=0, stdout="{bad-json", stderr=""), "STATISTICS_OUTPUT_INVALID"),
        (
            SimpleNamespace(
                exit_status=0,
                stdout=json.dumps({**statistics_payload("rem_client_new_to_market_speed.csv"), "unit": "us"}),
                stderr="",
            ),
            "STATISTICS_OUTPUT_INVALID",
        ),
    ],
)
async def test_statistics_execution_rejects_failed_or_invalid_script_output(
    client, admin_headers, tmp_path, monkeypatch, result, expected_code
):
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)
    install_statistics_fakes(monkeypatch, lambda _command: result)

    with SessionLocal() as db:
        run, _parser, step, artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        resource = db.get(Resource, parser_data["id"])
        select_statistics_inputs(db, run, step, [artifact.id], actor_id=1)
        with pytest.raises(WorkflowError) as exc:
            await execute_statistics_node(
                db,
                run,
                step,
                SimpleNamespace(node_type="data_statistics", config=step.config_snapshot),
                {"parser": resource},
            )
        assert exc.value.code == expected_code
        assert db.query(Metric).filter(Metric.run_id == run.id).count() == 0
        assert db.query(Artifact).filter(
            Artifact.run_id == run.id,
            Artifact.step_id == step.id,
            Artifact.artifact_type == "statistics_result_json",
        ).count() == 0
        assert step.result_summary["statistics_attempts"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_statistics_multifile_failure_does_not_keep_partial_metrics(
    client, admin_headers, tmp_path, monkeypatch
):
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)

    def handler(command):
        filename = Path(shlex.split(command)[1]).name
        if filename == "second.csv":
            return SimpleNamespace(exit_status=1, stdout="", stderr="failed")
        return SimpleNamespace(
            exit_status=0,
            stdout=json.dumps(statistics_payload(filename, 100)),
            stderr="",
        )

    install_statistics_fakes(monkeypatch, handler)

    with SessionLocal() as db:
        run, parser, step, artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        second = add_parsed_csv_artifact(db, run, parser, tmp_path, "second.csv")
        resource = db.get(Resource, parser_data["id"])
        select_statistics_inputs(db, run, step, [artifact.id, second.id], actor_id=1)
        with pytest.raises(WorkflowError) as exc:
            await execute_statistics_node(
                db,
                run,
                step,
                SimpleNamespace(node_type="data_statistics", config=step.config_snapshot),
                {"parser": resource},
            )
        assert exc.value.code == "STATISTICS_SCRIPT_FAILED"
        assert db.query(Metric).filter(Metric.run_id == run.id).count() == 0
        assert [item["status"] for item in step.result_summary["statistics_attempts"]] == [
            "succeeded", "failed",
        ]


@pytest.mark.asyncio
async def test_statistics_retry_replaces_previous_metrics(
    client, admin_headers, tmp_path, monkeypatch
):
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)
    values = iter([100.0, 250.0])

    def handler(command):
        filename = Path(shlex.split(command)[1]).name
        return SimpleNamespace(
            exit_status=0,
            stdout=json.dumps(statistics_payload(filename, next(values))),
            stderr="",
        )

    install_statistics_fakes(monkeypatch, handler)

    with SessionLocal() as db:
        run, _parser, step, artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        resource = db.get(Resource, parser_data["id"])
        select_statistics_inputs(db, run, step, [artifact.id], actor_id=1)
        for _ in range(2):
            await execute_statistics_node(
                db,
                run,
                step,
                SimpleNamespace(node_type="data_statistics", config=step.config_snapshot),
                {"parser": resource},
            )
        metrics = list(db.query(Metric).filter(Metric.run_id == run.id).all())
        assert len(metrics) == 2
        assert {item.detail["metric_key"]: item.value for item in metrics} == {
            "average": 250.0,
            "p99": 350.0,
        }


def test_statistics_script_list_endpoint(client, admin_headers, monkeypatch):
    resource = create_parser_resource(client, admin_headers)

    async def fake_list(_resource):
        return {
            "directory": "/tmp/parser",
            "files": [{
                "name": "statistics_cffex.py", "size": 120,
                "modified_at": "2026-07-28T10:00:00+08:00",
                "checksum": "a" * 64, "executable": True,
            }],
        }

    from app.api.routes import resource_configs

    monkeypatch.setattr(resource_configs.statistics_script_service, "list", fake_list)
    response = client.get(
        f"/api/v1/resources/{resource['id']}/statistics-scripts", headers=admin_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["files"][0]["checksum"] == "a" * 64

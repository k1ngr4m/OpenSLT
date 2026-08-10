from __future__ import annotations

import hashlib
import json
import shlex
import typing
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Artifact, DurableTask, Metric, Resource, RunResource, RunStep, TestRun as RunModel
from app.services import statistics_execution
from app.services.statistics_execution import (
    StatisticsScriptOutput,
    execute_statistics_node,
    list_statistics_csv_files,
    reserve_statistics_analysis,
    require_statistics_selection,
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
    parser.result_summary = {
        "remote_workdir": f"/tmp/parser/.openslt-runs/r{run.id}-s{parser.id}-a0-test",
        "output_files": [artifact.name],
    }
    return run, parser, statistics, artifact


class FakeSFTP:
    def __init__(self):
        self.put_calls = []

    async def makedirs(self, _path, exist_ok=False):
        return None

    async def put(self, _local, _remote):
        self.put_calls.append((_local, _remote))
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


def install_statistics_fakes(monkeypatch, handler, checksum: str = "a" * 64):
    connections = []

    async def fake_connect(**_kwargs):
        connection = FakeConnection(handler)
        connections.append(connection)
        return connection

    async def fake_read(_resource, filename):
        return {
            "name": filename, "checksum": checksum, "executable": True,
            "path": f"/tmp/parser/{filename}",
        }

    monkeypatch.setattr(statistics_execution.asyncssh, "connect", fake_connect)
    monkeypatch.setattr(statistics_execution.statistics_script_service, "read", fake_read)
    return connections


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


def select_legacy_inputs(step: RunStep, *artifacts: Artifact) -> dict[str, typing.Any]:
    selection = {
        "inputs": [
            {
                "artifact_id": artifact.id,
                "filename": artifact.name,
                "size": artifact.size,
                "checksum": artifact.checksum,
            }
            for artifact in artifacts
        ],
        "selected_by": 1,
        "selected_at": "2026-07-28T10:00:00+08:00",
    }
    step.result_summary = {**(step.result_summary or {}), "statistics_selection": selection}
    return selection


def response_error_code(response) -> str:
    payload = response.json()
    detail = payload.get("detail")
    if isinstance(detail, dict):
        return detail["code"]
    return payload["code"]


@pytest.mark.asyncio
async def test_statistics_csv_listing_filters_scope_and_file_types(
    client, admin_headers, tmp_path, monkeypatch
):
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)
    with SessionLocal() as db:
        run, parser, step, _artifact = create_statistics_run(
            db, plan["id"], scenario["id"], tmp_path
        )
        resource = db.get(Resource, parser_data["id"])
        parser.result_summary = {
            "remote_workdir": f"/tmp/parser/.openslt-runs/r{run.id}-s{parser.id}-a0-old",
            "output_files": ["old.csv"],
        }
        step.position = 4
        db.flush()
        failed_parser = RunStep(
            run_id=run.id, code="failed-parse", name="Failed Parse",
            node_type="parser_parse", position=2, status="failed", progress=100,
            config_snapshot={}, result_summary={
                "remote_workdir": "/tmp/parser/.openslt-runs/failed-a1",
                "output_files": ["failed.csv"],
            },
        )
        latest_parser = RunStep(
            run_id=run.id, code="latest-parse", name="Latest Parse",
            node_type="parser_parse", position=3, status="succeeded", progress=100,
            config_snapshot={}, result_summary={},
        )
        later_parser = RunStep(
            run_id=run.id, code="later-parse", name="Later Parse",
            node_type="parser_parse", position=5, status="succeeded", progress=100,
            config_snapshot={}, result_summary={
                "remote_workdir": "/tmp/parser/.openslt-runs/later-a0",
                "output_files": ["later.csv"],
            },
        )
        run.steps.extend([failed_parser, latest_parser, later_parser])
        db.flush()
        latest_directory = "/tmp/parser"
        latest_parser.result_summary = {
            "remote_workdir": latest_directory,
            "output_files": ["result.csv", "UPPER.CSV", "missing.csv", "nested.csv"],
        }

        def entry(name, file_type, size=10):
            return SimpleNamespace(
                filename=name,
                attrs=SimpleNamespace(type=file_type, size=size, mtime=100),
            )

        regular = statistics_execution.asyncssh.FILEXFER_TYPE_REGULAR
        directory = statistics_execution.asyncssh.FILEXFER_TYPE_DIRECTORY
        paths = {
            latest_directory: [
                entry("result.csv", regular), entry("UPPER.CSV", regular),
                entry("t_fut_orders.csv", regular), entry("unlisted.csv", regular),
                entry("nested.csv", directory), entry("note.txt", regular),
            ],
        }
        scanned_paths = []

        class ListingSFTP:
            def scandir(self, path):
                scanned_paths.append(path)
                async def generate():
                    for item in paths.get(path, []):
                        yield item
                return generate()

            def exit(self):
                return None

        class ListingConnection:
            async def start_sftp_client(self):
                return ListingSFTP()

            def close(self):
                return None

            async def wait_closed(self):
                return None

        async def fake_connect(**_kwargs):
            return ListingConnection()

        monkeypatch.setattr(statistics_execution.asyncssh, "connect", fake_connect)
        listing = await list_statistics_csv_files(resource, run, step)
        assert listing["directory"] == latest_directory
        assert scanned_paths == [latest_directory]
        assert [item["relative_path"] for item in listing["files"]] == [
            "UPPER.CSV",
            "result.csv",
        ]
        assert {item["source"] for item in listing["files"]} == {"current_run"}


@pytest.mark.asyncio
async def test_statistics_csv_listing_requires_valid_prior_parser_result(
    client, admin_headers, tmp_path
):
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)
    with SessionLocal() as db:
        run, parser, step, _artifact = create_statistics_run(
            db, plan["id"], scenario["id"], tmp_path
        )
        resource = db.get(Resource, parser_data["id"])
        parser.status = "failed"
        with pytest.raises(WorkflowError) as missing:
            await list_statistics_csv_files(resource, run, step)
        assert missing.value.code == "STATISTICS_PARSER_RESULT_REQUIRED"

        parser.status = "succeeded"
        parser.result_summary = {"output_files": ["result.csv"]}
        with pytest.raises(WorkflowError) as invalid:
            await list_statistics_csv_files(resource, run, step)
        assert invalid.value.code == "STATISTICS_PARSER_RESULT_INVALID"


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


def test_legacy_statistics_input_selection_rejects_other_artifacts(client, admin_headers, tmp_path):
    plan, scenario = create_plan_scenario(client, admin_headers)
    with SessionLocal() as db:
        run, _parser, step, artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        select_legacy_inputs(step, artifact)
        selection = require_statistics_selection(db, run, step)
        assert selection[0]["filename"] == artifact.name
        other = Artifact(
            run_id=run.id, step_id=step.id, artifact_type="statistics_result_json", name="wrong.csv",
            path=artifact.path, content_type="text/csv", size=artifact.size,
            checksum=artifact.checksum, is_immutable=True,
        )
        db.add(other)
        db.flush()
        with pytest.raises(WorkflowError) as changed:
            select_legacy_inputs(step, other)
            require_statistics_selection(db, run, step)
        assert changed.value.code == "STATISTICS_INPUT_INVALID"


def test_statistics_inputs_endpoint_rejects_invalid_path_and_wrong_state(
    client, admin_headers, tmp_path, monkeypatch
):
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)

    async def fake_list(_resource, _run, _step):
        return {
            "directory": "/tmp/parser/.openslt-runs/final",
            "files": [{
                "relative_path": "latency.csv", "filename": "latency.csv",
                "source": "current_run", "size": 12,
                "modified_at": "2026-07-28T10:00:00+08:00",
            }],
        }

    monkeypatch.setattr(statistics_execution, "list_statistics_csv_files", fake_list)
    from app.api.routes import runs as run_routes
    monkeypatch.setattr(run_routes, "list_statistics_csv_files", fake_list)
    with SessionLocal() as db:
        run, _parser, step, _artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        db.add(RunResource(run_id=run.id, resource_id=parser_data["id"], position=1))
        db.commit()
        run_id = run.id
        step_id = step.id

    response = client.get(
        f"/api/v1/runs/{run_id}/steps/{step_id}/statistics-csv-files",
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["files"][0]["relative_path"] == "latency.csv"

    response = client.put(
        f"/api/v1/runs/{run_id}/steps/{step_id}/statistics-inputs",
        headers=admin_headers,
        json={"relative_paths": ["../outside.csv"]},
    )
    assert response.status_code == 409
    assert response_error_code(response) == "STATISTICS_INPUT_INVALID"

    response = client.put(
        f"/api/v1/runs/{run_id}/steps/{step_id}/statistics-inputs",
        headers=admin_headers,
        json={"relative_paths": ["latency.csv"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["inputs"][0]["relative_path"] == "latency.csv"

    with SessionLocal() as db:
        stored = db.get(RunModel, run_id)
        stored.status = "running"
        db.commit()

    response = client.put(
        f"/api/v1/runs/{run_id}/steps/{step_id}/statistics-inputs",
        headers=admin_headers,
        json={"relative_paths": ["latency.csv"]},
    )
    assert response.status_code == 409
    assert response_error_code(response) == "STATISTICS_SELECTION_NOT_ALLOWED"


def test_statistics_runtime_config_and_reanalysis_state_machine(
    client, admin_headers, tmp_path, monkeypatch
):
    """配置变更必须原子化，且复算只能在统计节点等待人工完成时发起。"""
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)

    async def fake_list(_resource, _run, _step):
        return {
            "directory": "/tmp/parser/.openslt-runs/final",
            "files": [{
                "relative_path": "latency.csv", "filename": "latency.csv",
                "source": "current_run", "size": 12,
                "modified_at": "2026-07-28T10:00:00+08:00",
            }],
        }

    monkeypatch.setattr(statistics_execution, "list_statistics_csv_files", fake_list)
    from app.api.routes import runs as run_routes
    monkeypatch.setattr(run_routes, "list_statistics_csv_files", fake_list)
    monkeypatch.setattr(run_routes, "schedule_task", lambda _task_id: None)
    with SessionLocal() as db:
        run, _parser, step, _artifact = create_statistics_run(
            db, plan["id"], scenario["id"], tmp_path, run_number="R-STATISTICS-REANALYZE"
        )
        run.workflow_version_id = 1
        db.add(RunResource(run_id=run.id, resource_id=parser_data["id"], position=1))
        db.commit()
        run_id = run.id
        step_id = step.id

    config_url = f"/api/v1/runs/{run_id}/steps/{step_id}/statistics-config"
    first = client.put(
        config_url,
        headers=admin_headers,
        json={"relative_paths": ["latency.csv"], "max_latency_ns": 321},
    )
    assert first.status_code == 200, first.text
    assert first.json()["statistics_config_revision"] == 1
    assert first.json()["max_latency_ns"] == 321
    with SessionLocal() as db:
        stored_step = db.get(RunStep, step_id)
        assert stored_step.config_snapshot["max_latency_ns"] == 321
        assert stored_step.result_summary["statistics_config_revision"] == 1
        assert stored_step.result_summary["statistics_selection"]["inputs"] == [{
            "relative_path": "latency.csv",
            "filename": "latency.csv",
            "source": "current_run",
            "size": 12,
            "modified_at": "2026-07-28T10:00:00+08:00",
        }]

    # 相同配置不会生成虚假的修订或改变已持久化的输入快照。
    unchanged = client.put(
        config_url,
        headers=admin_headers,
        json={"relative_paths": ["latency.csv"], "max_latency_ns": 321},
    )
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["statistics_config_revision"] == 1

    with SessionLocal() as db:
        stored = db.get(RunModel, run_id)
        stored.status = "awaiting_step_completion"
        statistics_step = db.get(RunStep, step_id)
        statistics_step.status = "waiting"
        db.commit()

    changed_while_waiting = client.put(
        config_url,
        headers=admin_headers,
        json={"relative_paths": ["latency.csv"], "max_latency_ns": 654},
    )
    assert changed_while_waiting.status_code == 200, changed_while_waiting.text
    assert changed_while_waiting.json()["statistics_config_revision"] == 2

    # 旧输入端点仍可用，并复用同一份运行时配置/修订逻辑。
    legacy = client.put(
        f"/api/v1/runs/{run_id}/steps/{step_id}/statistics-inputs",
        headers=admin_headers,
        json={"relative_paths": ["latency.csv"]},
    )
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["inputs"][0]["relative_path"] == "latency.csv"

    analysis_url = f"/api/v1/runs/{run_id}/steps/{step_id}/analyze"
    first_analysis = client.post(analysis_url, headers=admin_headers)
    assert first_analysis.status_code == 200, first_analysis.text
    first_step = next(item for item in first_analysis.json()["steps"] if item["id"] == step_id)
    assert first_analysis.json()["status"] == "running"
    assert first_step["status"] == "running"
    assert first_step["retry_count"] == 0
    assert first_step["result_summary"]["statistics_analyses"][0]["analysis_no"] == 1
    assert first_step["result_summary"]["statistics_analyses"][0]["config_revision"] == 2
    assert first_step["result_summary"]["statistics_analyses"][0]["max_latency_ns"] == 654

    duplicate = client.post(analysis_url, headers=admin_headers)
    assert duplicate.status_code == 409

    with SessionLocal() as db:
        stored = db.get(RunModel, run_id)
        statistics_step = db.get(RunStep, step_id)
        history = statistics_step.result_summary["statistics_analyses"]
        history[0].update({"status": "succeeded", "finished_at": "2026-08-10T10:00:00+08:00"})
        statistics_step.result_summary = {
            **statistics_step.result_summary,
            "statistics_analyses": history,
            "statistics_latest_success_analysis_no": 1,
            "statistics_latest_success_revision": 2,
        }
        statistics_step.status = "waiting"
        stored.status = "awaiting_step_completion"
        db.commit()

    second_analysis = client.post(analysis_url, headers=admin_headers)
    assert second_analysis.status_code == 200, second_analysis.text
    second_step = next(item for item in second_analysis.json()["steps"] if item["id"] == step_id)
    assert second_step["retry_count"] == 0
    assert [item["analysis_no"] for item in second_step["result_summary"]["statistics_analyses"]] == [1, 2]

    with SessionLocal() as db:
        task = db.scalar(
            select(DurableTask)
            .where(DurableTask.run_id == run_id)
            .order_by(DurableTask.id.desc())
        )
        assert task is not None
        assert "analysis:2" in task.idempotency_key
        stored = db.get(RunModel, run_id)
        statistics_step = db.get(RunStep, step_id)
        history = statistics_step.result_summary["statistics_analyses"]
        history[-1].update({"status": "succeeded", "finished_at": "2026-08-10T10:01:00+08:00"})
        statistics_step.result_summary = {
            **statistics_step.result_summary,
            "statistics_analyses": history,
            "statistics_latest_success_analysis_no": 2,
            "statistics_latest_success_revision": 2,
        }
        statistics_step.status = "waiting"
        stored.status = "awaiting_step_completion"
        db.commit()

    stale = client.put(
        config_url,
        headers=admin_headers,
        json={"relative_paths": ["latency.csv"], "max_latency_ns": 987},
    )
    assert stale.status_code == 200, stale.text
    blocked_completion = client.post(
        f"/api/v1/runs/{run_id}/steps/{step_id}/complete", headers=admin_headers
    )
    assert blocked_completion.status_code == 409
    assert response_error_code(blocked_completion) == "STATISTICS_ANALYSIS_STALE"


def test_statistics_completion_rejects_legacy_step_after_runtime_config_change(
    client, admin_headers, tmp_path, monkeypatch
):
    """旧运行一旦保存新运行时配置，就不能绕过对应分析直接完成。"""
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)

    async def fake_list(_resource, _run, _step):
        return {
            "directory": "/tmp/parser/.openslt-runs/final",
            "files": [{
                "relative_path": "latency.csv", "filename": "latency.csv",
                "source": "current_run", "size": 12,
                "modified_at": "2026-07-28T10:00:00+08:00",
            }],
        }

    monkeypatch.setattr(statistics_execution, "list_statistics_csv_files", fake_list)
    from app.api.routes import runs as run_routes
    monkeypatch.setattr(run_routes, "list_statistics_csv_files", fake_list)
    with SessionLocal() as db:
        run, _parser, step, _artifact = create_statistics_run(
            db, plan["id"], scenario["id"], tmp_path, run_number="R-STATISTICS-LEGACY-CONFIG"
        )
        run.workflow_version_id = 1
        run.status = "awaiting_step_completion"
        step.status = "waiting"
        db.add(RunResource(run_id=run.id, resource_id=parser_data["id"], position=1))
        db.commit()
        run_id = run.id
        step_id = step.id

    configured = client.put(
        f"/api/v1/runs/{run_id}/steps/{step_id}/statistics-config",
        headers=admin_headers,
        json={"relative_paths": ["latency.csv"], "max_latency_ns": 321},
    )
    assert configured.status_code == 200, configured.text
    assert configured.json()["statistics_config_revision"] == 1

    completion = client.post(
        f"/api/v1/runs/{run_id}/steps/{step_id}/complete", headers=admin_headers
    )
    assert completion.status_code == 409
    assert response_error_code(completion) == "STATISTICS_ANALYSIS_STALE"


@pytest.mark.asyncio
async def test_statistics_execution_creates_metrics_and_json_artifact(
    client, admin_headers, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)
    commands = []

    def handler(command):
        commands.append(command)
        parts = shlex.split(command)
        payload = statistics_payload(Path(parts[1]).name, 1234.5)
        return SimpleNamespace(exit_status=0, stdout=json.dumps(payload), stderr="")

    connections = install_statistics_fakes(monkeypatch, handler)

    with SessionLocal() as db:
        run, _parser, step, artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        resource = db.get(Resource, parser_data["id"])
        select_legacy_inputs(step, artifact)
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
        assert result_artifact.artifact_type == "statistics_analysis_json"
        assert Path(result_artifact.path).is_file()
        assert shlex.split(commands[0])[1] == "/tmp/parser/rem_client_new_to_market_speed.csv"
        assert result["remote_workdir"] == "/tmp/parser"
        assert len(connections[-1].sftp.put_calls) == 1
        uploaded_path = connections[-1].sftp.put_calls[0][1]
        assert uploaded_path.startswith(
            "/tmp/parser/rem_client_new_to_market_speed.csv.openslt-"
        )
        assert ".openslt-runs" not in uploaded_path


@pytest.mark.asyncio
async def test_statistics_execution_finalizes_reserved_analysis_as_immutable_history_artifact(
    client, admin_headers, tmp_path, monkeypatch
):
    """若移除历史最终化或改回单一可覆写 JSON，此用例应失败。"""
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)
    install_statistics_fakes(monkeypatch, lambda command: SimpleNamespace(
        exit_status=0,
        stdout=json.dumps(statistics_payload(Path(shlex.split(command)[1]).name, 321.0)),
        stderr="",
    ))

    with SessionLocal() as db:
        run, _parser, step, artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        resource = db.get(Resource, parser_data["id"])
        select_legacy_inputs(step, artifact)
        assert reserve_statistics_analysis(db, run, step) == 1

        result = await execute_statistics_node(
            db, run, step,
            SimpleNamespace(node_type="data_statistics", config=step.config_snapshot),
            {"parser": resource},
        )

        history = result["statistics_analyses"]
        assert len(history) == 1
        record = history[0]
        assert record["status"] == "succeeded"
        assert record["artifact_id"] == result["statistics_artifact_id"]
        assert record["duration_ms"] >= 0
        assert record["artifact_checksum"]
        archived = db.get(Artifact, record["artifact_id"])
        assert archived.name == "statistics-analysis-v001.json"
        assert archived.artifact_type == "statistics_analysis_json"
        payload = json.loads(Path(archived.path).read_text(encoding="utf-8"))
        assert payload["analysis_no"] == 1
        assert payload["status"] == "succeeded"
        assert payload["results"][0]["metrics"][0]["value"] == 321.0


def test_statistics_analysis_artifact_reuses_orphaned_finalized_file(
    client, admin_headers, tmp_path, monkeypatch
):
    """若恢复覆盖“已落盘、未入库”的同号文件，此用例应失败。"""
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
    plan, scenario = create_plan_scenario(client, admin_headers)
    with SessionLocal() as db:
        run, _parser, step, _artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        target = statistics_execution._artifact_directory(run, step) / "statistics-analysis-v001.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        original = {
            "schema_version": 1,
            "analysis_no": 1,
            "status": "failed",
            "error": {"code": "PREVIOUS_FAILURE"},
        }
        target.write_text(json.dumps(original), encoding="utf-8")

        artifact = statistics_execution._register_analysis_artifact(
            db,
            run,
            step,
            1,
            {
                "analysis_no": 1,
                "status": "failed",
                "error": {"code": "PREVIOUS_FAILURE"},
            },
        )

        assert json.loads(target.read_text(encoding="utf-8")) == original
        assert artifact.checksum == hashlib.sha256(target.read_bytes()).hexdigest()
        assert artifact.is_immutable is True


def test_statistics_analysis_artifact_rejects_invalid_orphaned_file(
    client, admin_headers, tmp_path, monkeypatch
):
    """若恢复接受损坏的同号文件并覆盖它，此用例应失败。"""
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
    plan, scenario = create_plan_scenario(client, admin_headers)
    with SessionLocal() as db:
        run, _parser, step, _artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        target = statistics_execution._artifact_directory(run, step) / "statistics-analysis-v001.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"analysis_no": 2}', encoding="utf-8")

        with pytest.raises(WorkflowError) as exc:
            statistics_execution._register_analysis_artifact(
                db, run, step, 1, {"analysis_no": 1, "status": "failed"}
            )
        assert exc.value.code == "STATISTICS_ANALYSIS_ARTIFACT_CORRUPT"
        assert json.loads(target.read_text(encoding="utf-8"))["analysis_no"] == 2


def test_statistics_analysis_artifact_rejects_conflicting_orphaned_terminal_result(
    client, admin_headers, tmp_path, monkeypatch
):
    """若恢复将 orphan 的失败内容登记为新的成功，此用例应失败。"""
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
    plan, scenario = create_plan_scenario(client, admin_headers)
    with SessionLocal() as db:
        run, _parser, step, _artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        target = statistics_execution._artifact_directory(run, step) / "statistics-analysis-v001.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "schema_version": 1, "analysis_no": 1, "status": "failed",
        }), encoding="utf-8")

        with pytest.raises(WorkflowError) as exc:
            statistics_execution._register_analysis_artifact(
                db, run, step, 1, {"analysis_no": 1, "status": "succeeded"}
            )
        assert exc.value.code == "STATISTICS_ANALYSIS_ARTIFACT_CORRUPT"
        assert db.query(Artifact).filter(Artifact.run_id == run.id).count() == 1


def test_statistics_analysis_artifact_rejects_conflicting_orphaned_results(
    client, admin_headers, tmp_path, monkeypatch
):
    """若恢复把不同的成功结果绑定到同一不可变文件，此用例应失败。"""
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
    plan, scenario = create_plan_scenario(client, admin_headers)
    with SessionLocal() as db:
        run, _parser, step, _artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        target = statistics_execution._artifact_directory(run, step) / "statistics-analysis-v001.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "schema_version": 1, "analysis_no": 1, "status": "succeeded",
            "config_revision": 1, "inputs": [], "max_latency_ns": 100,
            "script": {"filename": "a.py", "checksum": "a"},
            "results": [{"source_file": "old.csv"}],
        }), encoding="utf-8")

        with pytest.raises(WorkflowError) as exc:
            statistics_execution._register_analysis_artifact(db, run, step, 1, {
                "schema_version": 1, "analysis_no": 1, "status": "succeeded",
                "config_revision": 1, "inputs": [], "max_latency_ns": 100,
                "script": {"filename": "a.py", "checksum": "a"},
                "results": [{"source_file": "new.csv"}],
            })
        assert exc.value.code == "STATISTICS_ANALYSIS_ARTIFACT_CORRUPT"


@pytest.mark.asyncio
async def test_statistics_missing_selection_is_archived_as_failed_analysis(
    client, admin_headers, tmp_path, monkeypatch
):
    """若执行前输入校验失败未预留并封存历史，此用例应失败。"""
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)
    with SessionLocal() as db:
        run, _parser, step, _artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        resource = db.get(Resource, parser_data["id"])

        with pytest.raises(WorkflowError) as exc:
            await execute_statistics_node(
                db, run, step,
                SimpleNamespace(node_type="data_statistics", config=step.config_snapshot),
                {"parser": resource},
            )
        assert exc.value.code == "STATISTICS_INPUTS_REQUIRED"
        record = step.result_summary["statistics_analyses"][-1]
        assert record["analysis_no"] == 1
        assert record["status"] == "failed"
        payload = json.loads(Path(db.get(Artifact, record["artifact_id"]).path).read_text(encoding="utf-8"))
        assert payload["error"]["code"] == "STATISTICS_INPUTS_REQUIRED"
        assert payload["inputs"] == []


@pytest.mark.asyncio
async def test_statistics_invalid_runtime_config_is_archived_as_failed_analysis(
    client, admin_headers, tmp_path, monkeypatch
):
    """若配置解析在最终化范围外抛出异常，此用例应失败。"""
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)
    with SessionLocal() as db:
        run, _parser, step, artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        resource = db.get(Resource, parser_data["id"])
        select_legacy_inputs(step, artifact)
        step.config_snapshot = {"max_latency_ns": 999999999}

        with pytest.raises(WorkflowError) as exc:
            await execute_statistics_node(
                db, run, step,
                SimpleNamespace(node_type="data_statistics", config=step.config_snapshot),
                {"parser": resource},
            )
        assert exc.value.code == "STATISTICS_SCRIPT_NAME_INVALID"
        record = step.result_summary["statistics_analyses"][-1]
        assert record["status"] == "failed"
        payload = json.loads(Path(db.get(Artifact, record["artifact_id"]).path).read_text(encoding="utf-8"))
        assert payload["error"]["code"] == "STATISTICS_SCRIPT_NAME_INVALID"
        assert payload["script"] == {"filename": "", "checksum": ""}


@pytest.mark.asyncio
async def test_statistics_list_runtime_config_is_archived_as_failed_analysis(
    client, admin_headers, tmp_path, monkeypatch
):
    """若真值非映射配置在预留前访问 .get()，此用例应失败。"""
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)
    with SessionLocal() as db:
        run, _parser, step, artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        resource = db.get(Resource, parser_data["id"])
        select_legacy_inputs(step, artifact)
        step.config_snapshot = ["invalid-config"]

        with pytest.raises(WorkflowError) as exc:
            await execute_statistics_node(
                db, run, step,
                SimpleNamespace(node_type="data_statistics", config=step.config_snapshot),
                {"parser": resource},
            )
        assert exc.value.code == "STATISTICS_CONFIG_INVALID"
        record = step.result_summary["statistics_analyses"][-1]
        assert record["status"] == "failed"
        assert record["inputs"][0]["artifact_id"] == artifact.id
        payload = json.loads(Path(db.get(Artifact, record["artifact_id"]).path).read_text(encoding="utf-8"))
        assert payload["error"]["code"] == "STATISTICS_CONFIG_INVALID"
        assert payload["script"] == {"filename": "", "checksum": ""}
        assert payload["max_latency_ns"] is None


@pytest.mark.asyncio
async def test_statistics_invalid_threshold_keeps_raw_failure_history_readable(
    client, admin_headers, tmp_path, monkeypatch
):
    """若坏阈值被伪装为默认值或令历史响应不可序列化，此用例应失败。"""
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)
    with SessionLocal() as db:
        run, _parser, step, artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        resource = db.get(Resource, parser_data["id"])
        select_legacy_inputs(step, artifact)
        step.config_snapshot = {**step.config_snapshot, "max_latency_ns": "not-an-integer"}

        with pytest.raises(WorkflowError) as exc:
            await execute_statistics_node(
                db, run, step,
                SimpleNamespace(node_type="data_statistics", config=step.config_snapshot),
                {"parser": resource},
            )
        assert exc.value.code == "STATISTICS_CONFIG_INVALID"
        record = step.result_summary["statistics_analyses"][-1]
        assert record["max_latency_ns"] is None
        payload = json.loads(Path(db.get(Artifact, record["artifact_id"]).path).read_text(encoding="utf-8"))
        assert payload["max_latency_ns"] is None


@pytest.mark.asyncio
async def test_statistics_finalization_failure_preserves_existing_metrics_and_summary(
    client, admin_headers, tmp_path, monkeypatch
):
    """若产物最终化失败后仍替换指标或顶层结果，此用例应失败。"""
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)
    install_statistics_fakes(monkeypatch, lambda command: SimpleNamespace(
        exit_status=0,
        stdout=json.dumps(statistics_payload(Path(shlex.split(command)[1]).name, 321.0)),
        stderr="",
    ))

    def fail_finalization(*_args, **_kwargs):
        raise WorkflowError("STATISTICS_ANALYSIS_ARTIFACT_CORRUPT", "无法封存分析结果", 409)

    monkeypatch.setattr(statistics_execution, "_finalize_statistics_analysis", fail_finalization)
    with SessionLocal() as db:
        run, _parser, step, artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        resource = db.get(Resource, parser_data["id"])
        select_legacy_inputs(step, artifact)
        step.result_summary = {
            **step.result_summary,
            "statistics_results": [{"source_file": "previous.csv", "metrics": []}],
            "statistics_artifact_id": 987,
        }
        db.add(Metric(
            run_id=run.id, name="Statistics/previous/平均值", value=88.0, unit="ns",
            detail={"statistics_step_id": step.id},
        ))

        with pytest.raises(WorkflowError) as exc:
            await execute_statistics_node(
                db, run, step,
                SimpleNamespace(node_type="data_statistics", config=step.config_snapshot),
                {"parser": resource},
            )
        assert exc.value.code == "STATISTICS_ANALYSIS_ARTIFACT_CORRUPT"
        metrics = list(db.query(Metric).filter(Metric.run_id == run.id).all())
        assert [(metric.name, metric.value) for metric in metrics] == [("Statistics/previous/平均值", 88.0)]
        assert step.result_summary["statistics_results"] == [{"source_file": "previous.csv", "metrics": []}]
        assert step.result_summary["statistics_artifact_id"] == 987


@pytest.mark.asyncio
async def test_statistics_failure_archives_partial_attempt_without_replacing_latest_metrics(
    client, admin_headers, tmp_path, monkeypatch
):
    """若失败覆盖成功指标或未保存部分多文件执行详情，此用例应失败。"""
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)

    def handler(command):
        filename = Path(shlex.split(command)[1]).name
        if filename == "second.csv":
            return SimpleNamespace(exit_status=1, stdout="", stderr="second failed")
        return SimpleNamespace(
            exit_status=0,
            stdout=json.dumps(statistics_payload(filename, 111.0)),
            stderr="",
        )

    install_statistics_fakes(monkeypatch, handler)
    with SessionLocal() as db:
        run, parser, step, artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        second = add_parsed_csv_artifact(db, run, parser, tmp_path, "second.csv")
        resource = db.get(Resource, parser_data["id"])
        select_legacy_inputs(step, artifact, second)
        step.result_summary = {
            **step.result_summary,
            "statistics_results": [{"source_file": "previous.csv", "metrics": []}],
            "statistics_artifact_id": 987,
            "statistics_latest_success_analysis_no": 7,
            "statistics_latest_success_revision": 3,
        }
        db.add(Metric(
            run_id=run.id, name="Statistics/previous/平均值", value=88.0, unit="ns",
            detail={"statistics_step_id": step.id},
        ))
        reserve_statistics_analysis(db, run, step)

        with pytest.raises(WorkflowError) as exc:
            await execute_statistics_node(
                db, run, step,
                SimpleNamespace(node_type="data_statistics", config=step.config_snapshot),
                {"parser": resource},
            )
        assert exc.value.code == "STATISTICS_SCRIPT_FAILED"

        record = step.result_summary["statistics_analyses"][0]
        assert record["status"] == "failed"
        archived = db.get(Artifact, record["artifact_id"])
        payload = json.loads(Path(archived.path).read_text(encoding="utf-8"))
        assert [item["status"] for item in payload["attempts"]] == ["succeeded", "failed"]
        assert step.result_summary["statistics_results"] == [{"source_file": "previous.csv", "metrics": []}]
        assert step.result_summary["statistics_artifact_id"] == 987
        assert step.result_summary["statistics_latest_success_analysis_no"] == 7
        assert step.result_summary["statistics_latest_success_revision"] == 3
        assert db.query(Metric).filter(Metric.run_id == run.id).count() == 1


def test_statistics_analysis_history_api_is_newest_first_and_integrity_checked(
    client, admin_headers, tmp_path
):
    """若历史 API 泄漏完整结果、未校验校验和或限制访客读取，此用例应失败。"""
    plan, scenario = create_plan_scenario(client, admin_headers)
    with SessionLocal() as db:
        run, _parser, step, _artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        first_path = tmp_path / "statistics-analysis-v001.json"
        second_path = tmp_path / "statistics-analysis-v002.json"
        first_data = json.dumps({"analysis_no": 1, "status": "succeeded", "results": [{"source_file": "old.csv"}]}).encode()
        second_data = json.dumps({"analysis_no": 2, "status": "failed", "attempts": [{"source_file": "new.csv", "status": "failed"}]}).encode()
        first_path.write_bytes(first_data)
        second_path.write_bytes(second_data)
        first = Artifact(run_id=run.id, step_id=step.id, artifact_type="statistics_analysis_json", name=first_path.name, path=str(first_path), content_type="application/json", size=len(first_data), checksum=hashlib.sha256(first_data).hexdigest(), is_immutable=True)
        second = Artifact(run_id=run.id, step_id=step.id, artifact_type="statistics_analysis_json", name=second_path.name, path=str(second_path), content_type="application/json", size=len(second_data), checksum=hashlib.sha256(second_data).hexdigest(), is_immutable=True)
        db.add_all([first, second])
        db.flush()
        step.result_summary = {
            "statistics_analyses": [
                {"analysis_no": 1, "status": "succeeded", "config_revision": 1, "artifact_id": first.id, "artifact_checksum": first.checksum, "artifact_size": first.size, "inputs": [], "max_latency_ns": 100, "script": {}, "reserved_at": "2026-08-10T10:00:00+08:00", "finished_at": "2026-08-10T10:00:01+08:00", "duration_ms": 1},
                {"analysis_no": 2, "status": "failed", "config_revision": 2, "artifact_id": second.id, "artifact_checksum": second.checksum, "artifact_size": second.size, "inputs": [], "max_latency_ns": 200, "script": {}, "reserved_at": "2026-08-10T10:01:00+08:00", "finished_at": "2026-08-10T10:01:01+08:00", "duration_ms": 1, "error_code": "STATISTICS_SCRIPT_FAILED"},
            ]
        }
        db.commit()
        run_id, step_id, first_artifact_id = run.id, step.id, first.id

    visitor = client.post("/api/v1/users", headers=admin_headers, json={
        "username": "statistics-history-viewer", "display_name": "历史访客",
        "password": "viewer-password", "role": "visitor",
    })
    assert visitor.status_code == 201, visitor.text
    token = client.post("/api/v1/auth/login", json={
        "username": "statistics-history-viewer", "password": "viewer-password",
    }).json()["access_token"]
    visitor_headers = {"Authorization": f"Bearer {token}"}
    base = f"/api/v1/runs/{run_id}/steps/{step_id}/statistics-analyses"

    listed = client.get(base, headers=visitor_headers)
    assert listed.status_code == 200, listed.text
    assert [item["analysis_no"] for item in listed.json()] == [2, 1]
    assert "attempts" not in listed.json()[0]
    detail = client.get(f"{base}/2", headers=visitor_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["artifact"]["attempts"][0]["source_file"] == "new.csv"
    assert client.get(f"{base}/99", headers=visitor_headers).status_code == 404

    second_path.write_text("corrupt", encoding="utf-8")
    corrupt = client.get(f"{base}/2", headers=visitor_headers)
    assert corrupt.status_code == 409
    assert response_error_code(corrupt) == "STATISTICS_ANALYSIS_ARTIFACT_CORRUPT"

    with SessionLocal() as db:
        db.delete(db.get(Artifact, first_artifact_id))
        db.commit()
    missing_artifact = client.get(f"{base}/1", headers=visitor_headers)
    assert missing_artifact.status_code == 404
    assert response_error_code(missing_artifact) == "STATISTICS_ANALYSIS_ARTIFACT_MISSING"


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
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)
    install_statistics_fakes(monkeypatch, lambda _command: result)

    with SessionLocal() as db:
        run, _parser, step, artifact = create_statistics_run(db, plan["id"], scenario["id"], tmp_path)
        resource = db.get(Resource, parser_data["id"])
        select_legacy_inputs(step, artifact)
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
        record = step.result_summary["statistics_analyses"][-1]
        assert record["status"] == "failed"
        assert db.get(Artifact, record["artifact_id"]).artifact_type == "statistics_analysis_json"


@pytest.mark.asyncio
async def test_statistics_multifile_failure_does_not_keep_partial_metrics(
    client, admin_headers, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
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
        select_legacy_inputs(step, artifact, second)
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
        record = step.result_summary["statistics_analyses"][-1]
        archived = db.get(Artifact, record["artifact_id"])
        assert [item["status"] for item in json.loads(Path(archived.path).read_text())["attempts"]] == [
            "succeeded", "failed",
        ]


@pytest.mark.asyncio
async def test_statistics_retry_replaces_previous_metrics(
    client, admin_headers, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
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
        select_legacy_inputs(step, artifact)
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
        assert [item["analysis_no"] for item in step.result_summary["statistics_analyses"]] == [1, 2]
        assert step.result_summary["statistics_latest_success_analysis_no"] == 2


@pytest.mark.asyncio
async def test_statistics_executes_selected_remote_csv_without_upload(
    client, admin_headers, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)
    listing = {
        "directory": "/tmp/parser/.openslt-runs/final",
        "files": [{
            "relative_path": "latency.csv", "filename": "latency.csv",
            "source": "current_run", "size": 12,
            "modified_at": "2026-07-28T10:00:00+08:00",
        }],
    }

    async def fake_list(_resource, _run, _step):
        return listing

    commands = []

    def handler(command):
        commands.append(command)
        return SimpleNamespace(
            exit_status=0,
            stdout=json.dumps(statistics_payload("latency.csv", 100)),
            stderr="",
        )

    monkeypatch.setattr(statistics_execution, "list_statistics_csv_files", fake_list)
    connections = install_statistics_fakes(monkeypatch, handler)
    with SessionLocal() as db:
        run, _parser, step, _artifact = create_statistics_run(
            db, plan["id"], scenario["id"], tmp_path
        )
        resource = db.get(Resource, parser_data["id"])
        await select_statistics_inputs(
            db, run, step, resource, ["latency.csv"], actor_id=1
        )
        result = await execute_statistics_node(
            db, run, step,
            SimpleNamespace(node_type="data_statistics", config=step.config_snapshot),
            {"parser": resource},
        )
        assert shlex.split(commands[0])[1] == "/tmp/parser/.openslt-runs/final/latency.csv"
        assert connections[-1].sftp.put_calls == []
        assert result["statistics_results"][0]["source_path"] == "latency.csv"
        metric = db.query(Metric).filter(Metric.run_id == run.id).first()
        assert metric.detail["source_path"] == "latency.csv"
        assert metric.detail["source_artifact_id"] is None


@pytest.mark.asyncio
async def test_statistics_rejects_remote_csv_changed_after_selection(
    client, admin_headers, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "artifact_root", tmp_path / "artifacts")
    parser_data = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(client, admin_headers)
    current_size = 12

    async def fake_list(_resource, _run, _step):
        return {
            "directory": "/tmp/parser/.openslt-runs/final",
            "files": [{
                "relative_path": "latency.csv", "filename": "latency.csv",
                "source": "current_run", "size": current_size,
                "modified_at": "2026-07-28T10:00:00+08:00",
            }],
        }

    monkeypatch.setattr(statistics_execution, "list_statistics_csv_files", fake_list)
    with SessionLocal() as db:
        run, _parser, step, _artifact = create_statistics_run(
            db, plan["id"], scenario["id"], tmp_path
        )
        resource = db.get(Resource, parser_data["id"])
        await select_statistics_inputs(
            db, run, step, resource, ["latency.csv"], actor_id=1
        )
        current_size = 13
        with pytest.raises(WorkflowError) as changed:
            await execute_statistics_node(
                db, run, step,
                SimpleNamespace(node_type="data_statistics", config=step.config_snapshot),
                {"parser": resource},
            )
        assert changed.value.code == "STATISTICS_INPUT_CHANGED"


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

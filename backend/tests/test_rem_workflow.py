from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from app.core.database import SessionLocal
from app.models import LogRecord, Resource
from app.services import rem_execution
from conftest import create_plan_scenario, create_resource, publish_workflow


DEFAULT_COMMANDS = ["./stop_rem.sh", "./makeneat.sh", "./start_rem_all.sh"]


def rem_node(commands=None):
    config = {} if commands is None else {"commands": commands}
    return {
        "node_key": "rem-startup",
        "node_type": "rem_startup",
        "name": "启动rem柜台",
        "config": config,
    }


def create_rem_run(client, headers, commands=None):
    resource = create_resource(client, headers, "REM-startup")
    plan, scenario = create_plan_scenario(
        client, headers, required_types=["rem"], resource_ids=[resource["id"]]
    )
    publish_workflow(client, headers, scenario, [resource["id"]], [rem_node(commands)])
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
    return resource, response.json()


def start_workflow_and_step(client, headers, run_id):
    response = client.post(f"/api/v1/runs/{run_id}/start", headers=headers)
    assert response.status_code == 200, response.text
    run = client.get(f"/api/v1/runs/{run_id}", headers=headers).json()
    step = run["steps"][0]
    response = client.post(
        f"/api/v1/runs/{run_id}/steps/{step['id']}/start", headers=headers
    )
    assert response.status_code == 200, response.text
    return client.get(f"/api/v1/runs/{run_id}", headers=headers).json()


class LocalShellConnection:
    def __init__(self, transform=None):
        self.closed = False
        self.inputs = []
        self.transform = transform

    async def run(self, command, *, input, check=False):
        assert command == "cd /tmp/openslt && /bin/sh -s"
        assert check is False
        self.inputs.append(input)
        script = self.transform(input, len(self.inputs)) if self.transform else input
        completed = subprocess.run(
            ["/bin/sh", "-s"],
            input=script,
            text=True,
            capture_output=True,
            check=False,
            cwd="/tmp",
        )
        return SimpleNamespace(
            exit_status=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


def test_rem_startup_publish_requires_bound_rem_resource(client, admin_headers):
    market = create_resource(client, admin_headers, "Market-only", resource_type="market")
    _, scenario = create_plan_scenario(
        client, admin_headers, required_types=["market"], resource_ids=[market["id"]]
    )
    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers
    ).json()
    saved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": [market["id"]],
            "nodes": [rem_node()],
        },
    )
    assert saved.status_code == 200, saved.text
    assert "启动 REM 柜台需要绑定已启用的 REM 资源" in {
        item["message"] for item in saved.json()["validation_errors"]
    }
    published = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers
    )
    assert published.status_code == 422


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("is_enabled", False, "启动 REM 柜台需要绑定已启用的 REM 资源"),
        ("remote_path", "", "REM 资源未配置远端路径"),
    ],
)
def test_rem_startup_validates_bound_resource_state(
    client, admin_headers, field, value, message
):
    resource = create_resource(client, admin_headers, f"REM-{field}")
    _, scenario = create_plan_scenario(
        client, admin_headers, required_types=["rem"], resource_ids=[resource["id"]]
    )
    with SessionLocal() as db:
        record = db.get(Resource, resource["id"])
        setattr(record, field, value)
        db.commit()

    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers
    ).json()
    saved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": [resource["id"]],
            "nodes": [rem_node()],
        },
    )
    if field == "is_enabled":
        assert saved.status_code == 400, saved.text
        return
    assert saved.status_code == 200, saved.text
    assert message in {item["message"] for item in saved.json()["validation_errors"]}


def test_empty_commands_can_be_saved_but_not_published(client, admin_headers):
    resource = create_resource(client, admin_headers, "REM-empty")
    _, scenario = create_plan_scenario(
        client, admin_headers, required_types=["rem"], resource_ids=[resource["id"]]
    )
    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers
    ).json()
    saved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": [resource["id"]],
            "nodes": [rem_node([" ", "\n"])],
        },
    )
    assert saved.status_code == 200, saved.text
    node = saved.json()["draft"]["nodes"][0]
    assert node["config"]["commands"] == []
    assert {item["field"] for item in saved.json()["validation_errors"]} == {"commands"}
    published = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers
    )
    assert published.status_code == 422


def test_rem_startup_runs_configured_commands_in_shared_shell(
    client, admin_headers, monkeypatch
):
    connection = LocalShellConnection()

    async def fake_connect(**options):
        assert options["password"] == "secret"
        return connection

    monkeypatch.setattr(rem_execution.asyncssh, "connect", fake_connect)
    commands = [
        "export OPENSLT_MODE=shared",
        "mkdir -p state && cd state",
        "printf '%s:%s' \"$OPENSLT_MODE\" \"$PWD\"",
    ]
    resource, run = create_rem_run(client, admin_headers, commands)
    executed = start_workflow_and_step(client, admin_headers, run["id"])

    assert executed["status"] == "awaiting_step_completion"
    assert len(connection.inputs) == 1
    assert connection.closed is True
    step = executed["steps"][0]
    assert step["config_snapshot"]["commands"] == commands
    results = step["result_summary"]["commands"]
    assert [item["command"] for item in results] == commands
    assert results[2]["stdout"].startswith("shared:")
    assert results[2]["stdout"].endswith("/state")
    assert all(item["exit_code"] == 0 for item in results)
    assert step["result_summary"]["resource_id"] == resource["id"]
    with SessionLocal() as db:
        logs = db.query(LogRecord).filter(
            LogRecord.run_id == run["id"],
            LogRecord.event == "rem.command_completed",
        ).order_by(LogRecord.id).all()
        assert [item.detail["index"] for item in logs] == [1, 2, 3]
        assert [item.detail["command"] for item in logs] == commands


@pytest.mark.parametrize("failed_index", [0, 1, 2])
def test_rem_startup_failure_stops_and_retry_restarts_from_first_command(
    client, admin_headers, monkeypatch, failed_index
):
    connection = LocalShellConnection(
        transform=None
    )

    async def fake_connect(**_options):
        return connection

    monkeypatch.setattr(rem_execution.asyncssh, "connect", fake_connect)
    commands = ["printf one", "printf two", "printf three"]
    commands[failed_index] = "printf failed >&2; false"
    failure_pending = True

    def transform(script, attempt):
        nonlocal failure_pending
        if attempt == 1 and failure_pending:
            failure_pending = False
            return script.replace("printf failed >&2; false", "printf failed >&2; (exit 7)")
        return script.replace("printf failed >&2; false", "printf recovered")

    connection.transform = transform
    _, run = create_rem_run(client, admin_headers, commands)
    failed = start_workflow_and_step(client, admin_headers, run["id"])

    assert failed["status"] == "awaiting_step_retry"
    assert f"第 {failed_index + 1} 条" in failed["steps"][0]["error_message"]
    with SessionLocal() as db:
        command_logs = db.query(LogRecord).filter(
            LogRecord.run_id == run["id"], LogRecord.event.like("rem.command_%")
        ).order_by(LogRecord.id).all()
        assert len(command_logs) == failed_index + 1
        assert command_logs[-1].event == "rem.command_failed"

    step = failed["steps"][0]
    retried = client.post(
        f"/api/v1/runs/{run['id']}/steps/{step['id']}/retry", headers=admin_headers
    )
    assert retried.status_code == 200, retried.text
    retried_run = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert retried_run["status"] == "awaiting_step_completion"
    assert len(connection.inputs) == 2
    assert [item["index"] for item in retried_run["steps"][0]["result_summary"]["commands"]] == [1, 2, 3]


def test_rem_startup_reports_shell_termination(client, admin_headers, monkeypatch):
    connection = LocalShellConnection()

    async def fake_connect(**_options):
        return connection

    monkeypatch.setattr(rem_execution.asyncssh, "connect", fake_connect)
    commands = ["printf before", "printf terminating; exec true", "printf never"]
    _, run = create_rem_run(client, admin_headers, commands)
    failed = start_workflow_and_step(client, admin_headers, run["id"])

    assert failed["status"] == "awaiting_step_retry"
    assert "第 2 条" in failed["steps"][0]["error_message"]
    assert "Shell 提前终止" in failed["steps"][0]["error_message"]
    result = failed["steps"][0]["result_summary"]
    assert [item["command"] for item in result["commands"]] == commands[:2]
    assert result["commands"][1]["stdout"] == "terminating"
    assert result["commands"][1]["shell_terminated"] is True
    assert result["exit_code"] == 1
    assert result["duration_ms"] >= 0
    with SessionLocal() as db:
        failed_log = db.query(LogRecord).filter(
            LogRecord.run_id == run["id"], LogRecord.event == "rem.command_failed"
        ).one()
        assert failed_log.detail["index"] == 2
        assert failed_log.detail["shell_terminated"] is True


def test_rem_startup_reports_ssh_failure(client, admin_headers, monkeypatch):
    async def fake_connect(**_options):
        raise RuntimeError("password=top-secret connection refused")

    monkeypatch.setattr(rem_execution.asyncssh, "connect", fake_connect)
    _, run = create_rem_run(client, admin_headers, ["true"])
    failed = start_workflow_and_step(client, admin_headers, run["id"])

    assert failed["status"] == "awaiting_step_retry"
    assert "connection refused" in failed["steps"][0]["error_message"]
    assert "top-secret" not in failed["steps"][0]["error_message"]


def test_legacy_empty_config_uses_default_commands():
    config = rem_execution.parse_node_config("rem_startup", {})
    assert config.commands == DEFAULT_COMMANDS

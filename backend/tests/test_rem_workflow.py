from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.database import SessionLocal
from app.models import LogRecord, Resource
from app.services import rem_execution
from conftest import create_plan_scenario, create_resource, publish_workflow


REM_NODE = {
    "node_key": "rem-startup",
    "node_type": "rem_startup",
    "name": "启动rem柜台",
    "config": {},
}
EXPECTED_COMMANDS = [
    "cd /tmp/openslt && ./stop_rem.sh",
    "cd /tmp/openslt && ./makeneat.sh",
    "cd /tmp/openslt && ./start_rem_all.sh",
]
EXPECTED_SCRIPTS = ["./stop_rem.sh", "./makeneat.sh", "./start_rem_all.sh"]


def create_rem_run(client, headers):
    resource = create_resource(client, headers, "REM-startup")
    plan, scenario = create_plan_scenario(
        client, headers, required_types=["rem"], resource_ids=[resource["id"]]
    )
    publish_workflow(client, headers, scenario, [resource["id"]], [REM_NODE])
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
            "nodes": [REM_NODE],
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
    assert published.json()["code"] == "WORKFLOW_VALIDATION_FAILED"


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
            "nodes": [REM_NODE],
        },
    )
    if field == "is_enabled":
        assert saved.status_code == 400, saved.text
        assert saved.json()["code"] == "INVALID_RESOURCES"
        return
    assert saved.status_code == 200, saved.text
    assert message in {item["message"] for item in saved.json()["validation_errors"]}


def test_rem_startup_executes_fixed_commands_in_one_connection(
    client, admin_headers, monkeypatch
):
    commands = []
    connections = []

    class FakeConnection:
        def __init__(self):
            self.closed = False

        async def run(self, command, check=False):
            assert check is False
            commands.append(command)
            return SimpleNamespace(exit_status=0, stdout=f"done: {command}", stderr="")

        def close(self):
            self.closed = True

        async def wait_closed(self):
            return None

    async def fake_connect(**options):
        assert options["password"] == "secret"
        connection = FakeConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(rem_execution.asyncssh, "connect", fake_connect)
    resource, run = create_rem_run(client, admin_headers)
    executed = start_workflow_and_step(client, admin_headers, run["id"])

    assert executed["status"] == "awaiting_step_completion"
    assert commands == EXPECTED_COMMANDS
    assert len(connections) == 1
    assert connections[0].closed is True
    step = executed["steps"][0]
    assert step["status"] == "waiting"
    assert step["result_summary"]["resource_id"] == resource["id"]
    assert step["result_summary"]["resource_name"] == resource["name"]
    assert step["result_summary"]["remote_workdir"] == "/tmp/openslt"
    assert [item["script"] for item in step["result_summary"]["commands"]] == EXPECTED_SCRIPTS
    assert all(item["exit_code"] == 0 for item in step["result_summary"]["commands"])
    with SessionLocal() as db:
        logs = db.query(LogRecord).filter(
            LogRecord.run_id == run["id"],
            LogRecord.event == "rem.command_completed",
        ).order_by(LogRecord.id).all()
        assert [item.detail["script"] for item in logs] == EXPECTED_SCRIPTS
        assert all(item.detail["exit_code"] == 0 for item in logs)


@pytest.mark.parametrize("failed_index", [0, 1, 2])
def test_rem_startup_failure_stops_sequence_and_retry_restarts_from_stop(
    client, admin_headers, monkeypatch, failed_index
):
    commands = []
    failure_pending = True

    class FakeConnection:
        async def run(self, command, check=False):
            nonlocal failure_pending
            assert check is False
            commands.append(command)
            command_index = EXPECTED_COMMANDS.index(command)
            if failure_pending and command_index == failed_index:
                failure_pending = False
                return SimpleNamespace(exit_status=7, stdout="", stderr="permission denied")
            return SimpleNamespace(exit_status=0, stdout="ok", stderr="")

        def close(self):
            return None

        async def wait_closed(self):
            return None

    async def fake_connect(**_options):
        return FakeConnection()

    monkeypatch.setattr(rem_execution.asyncssh, "connect", fake_connect)
    _, run = create_rem_run(client, admin_headers)
    failed = start_workflow_and_step(client, admin_headers, run["id"])

    assert failed["status"] == "awaiting_step_retry"
    assert failed["steps"][0]["status"] == "failed"
    assert "退出码 7" in failed["steps"][0]["error_message"]
    assert commands == EXPECTED_COMMANDS[: failed_index + 1]
    with SessionLocal() as db:
        failed_log = db.query(LogRecord).filter(
            LogRecord.run_id == run["id"],
            LogRecord.event == "rem.command_failed",
        ).one()
        assert failed_log.detail["script"] == EXPECTED_SCRIPTS[failed_index]
        assert failed_log.detail["exit_code"] == 7

    step = failed["steps"][0]
    retried = client.post(
        f"/api/v1/runs/{run['id']}/steps/{step['id']}/retry", headers=admin_headers
    )
    assert retried.status_code == 200, retried.text
    retried_run = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert retried_run["status"] == "awaiting_step_completion"
    assert retried_run["steps"][0]["status"] == "waiting"
    assert commands == EXPECTED_COMMANDS[: failed_index + 1] + EXPECTED_COMMANDS

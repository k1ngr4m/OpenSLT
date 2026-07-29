from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from app.core.database import SessionLocal
from app.models import LogRecord
from app.services import market_execution
from app.services.market_scripts import market_script_service
from conftest import create_plan_scenario, create_resource, publish_workflow


SCRIPT_CONTENTS = {
    "prepare.sh": b"#!/bin/sh\necho prepare\n",
    "start_all.sh": b"#!/bin/sh\necho start\n",
}
SCRIPT_CHECKSUMS = {
    name: hashlib.sha256(content).hexdigest() for name, content in SCRIPT_CONTENTS.items()
}
MARKET_NODE = {
    "node_key": "market-startup",
    "node_type": "market_startup",
    "name": "启动模拟市场",
    "config": {
        "scripts": [
            {"filename": "prepare.sh", "checksum": SCRIPT_CHECKSUMS["prepare.sh"]},
            {"filename": "start_all.sh", "checksum": SCRIPT_CHECKSUMS["start_all.sh"]},
        ],
    },
}
EXPECTED_COMMANDS = [
    "cd /tmp/openslt && ./prepare.sh",
    "cd /tmp/openslt && ./start_all.sh",
]


async def valid_script_details(_resource, filenames, **_kwargs):
    return [
        {
            "name": filename,
            "checksum": SCRIPT_CHECKSUMS[filename],
            "executable": True,
            "path": f"/tmp/openslt/{filename}",
        }
        for filename in filenames
    ]


def create_market_run(client, headers, monkeypatch):
    monkeypatch.setattr(market_script_service, "read_many", valid_script_details)
    resource = create_resource(client, headers, "Market-startup", resource_type="market")
    plan, scenario = create_plan_scenario(
        client, headers, required_types=["market"], resource_ids=[resource["id"]]
    )
    publish_workflow(client, headers, scenario, [resource["id"]], [MARKET_NODE])
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
    return resource, scenario, response.json()


def start_workflow_and_step(client, headers, run_id):
    assert client.post(f"/api/v1/runs/{run_id}/start", headers=headers).status_code == 200
    run = client.get(f"/api/v1/runs/{run_id}", headers=headers).json()
    step = run["steps"][0]
    response = client.post(
        f"/api/v1/runs/{run_id}/steps/{step['id']}/start", headers=headers
    )
    assert response.status_code == 200, response.text
    return client.get(f"/api/v1/runs/{run_id}", headers=headers).json()


def test_market_startup_draft_requires_resource_and_at_least_one_script(
    client, admin_headers
):
    rem = create_resource(client, admin_headers, "REM-only")
    _, scenario = create_plan_scenario(
        client, admin_headers, required_types=["rem"], resource_ids=[rem["id"]]
    )
    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers
    ).json()
    node = {
        "node_key": "market-empty",
        "node_type": "market_startup",
        "name": "启动模拟市场",
        "config": {"scripts": []},
    }
    saved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": [rem["id"]],
            "nodes": [node],
        },
    )
    assert saved.status_code == 200, saved.text
    messages = {item["message"] for item in saved.json()["validation_errors"]}
    assert "启动模拟市场需要绑定已启用的模拟市场资源" in messages
    assert "至少选择一个模拟市场启动脚本" in messages


@pytest.mark.parametrize(
    ("executable", "checksum", "message"),
    [
        (False, SCRIPT_CHECKSUMS["prepare.sh"], "没有执行权限"),
        (True, "f" * 64, "已发生变化"),
    ],
)
def test_market_startup_publish_revalidates_selected_scripts(
    client, admin_headers, monkeypatch, executable, checksum, message
):
    async def invalid_details(_resource, filenames, **_kwargs):
        details = await valid_script_details(_resource, filenames)
        details[0]["executable"] = executable
        details[0]["checksum"] = checksum
        return details

    monkeypatch.setattr(market_script_service, "read_many", invalid_details)
    resource = create_resource(client, admin_headers, "Market-publish", resource_type="market")
    _, scenario = create_plan_scenario(
        client, admin_headers, required_types=["market"], resource_ids=[resource["id"]]
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
            "nodes": [MARKET_NODE],
        },
    )
    assert saved.status_code == 200, saved.text
    published = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers
    )
    assert published.status_code == 422
    assert message in published.text


def test_market_startup_executes_scripts_in_configured_order(
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
            return SimpleNamespace(exit_status=0, stdout="started", stderr="")

        def close(self):
            self.closed = True

        async def wait_closed(self):
            return None

    async def fake_connect(**_options):
        connection = FakeConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(market_execution.asyncssh, "connect", fake_connect)
    resource, _, run = create_market_run(client, admin_headers, monkeypatch)
    executed = start_workflow_and_step(client, admin_headers, run["id"])

    assert executed["status"] == "awaiting_step_completion"
    assert commands == EXPECTED_COMMANDS
    assert len(connections) == 1
    assert connections[0].closed is True
    summary = executed["steps"][0]["result_summary"]
    assert summary["resource_id"] == resource["id"]
    assert [item["script"] for item in summary["commands"]] == ["prepare.sh", "start_all.sh"]
    with SessionLocal() as db:
        logs = db.query(LogRecord).filter(
            LogRecord.run_id == run["id"],
            LogRecord.event == "market.command_completed",
        ).order_by(LogRecord.id).all()
        assert [item.detail["script"] for item in logs] == ["prepare.sh", "start_all.sh"]


@pytest.mark.parametrize("failed_index", [0, 1])
def test_market_startup_failure_stops_and_retry_restarts_from_first_script(
    client, admin_headers, monkeypatch, failed_index
):
    commands = []
    failure_pending = True

    class FakeConnection:
        async def run(self, command, check=False):
            nonlocal failure_pending
            commands.append(command)
            index = EXPECTED_COMMANDS.index(command)
            if failure_pending and index == failed_index:
                failure_pending = False
                return SimpleNamespace(exit_status=9, stdout="", stderr="failed")
            return SimpleNamespace(exit_status=0, stdout="ok", stderr="")

        def close(self):
            return None

        async def wait_closed(self):
            return None

    async def fake_connect(**_options):
        return FakeConnection()

    monkeypatch.setattr(market_execution.asyncssh, "connect", fake_connect)
    _, _, run = create_market_run(client, admin_headers, monkeypatch)
    failed = start_workflow_and_step(client, admin_headers, run["id"])
    assert failed["status"] == "awaiting_step_retry"
    assert commands == EXPECTED_COMMANDS[: failed_index + 1]

    step = failed["steps"][0]
    response = client.post(
        f"/api/v1/runs/{run['id']}/steps/{step['id']}/retry", headers=admin_headers
    )
    assert response.status_code == 200, response.text
    retried = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert retried["status"] == "awaiting_step_completion"
    assert commands == EXPECTED_COMMANDS[: failed_index + 1] + EXPECTED_COMMANDS


def test_market_startup_runtime_rejects_changed_script_before_execution(
    client, admin_headers, monkeypatch
):
    commands = []

    class FakeConnection:
        async def run(self, command, check=False):
            commands.append(command)
            return SimpleNamespace(exit_status=0, stdout="", stderr="")

        def close(self):
            return None

        async def wait_closed(self):
            return None

    async def fake_connect(**_options):
        return FakeConnection()

    monkeypatch.setattr(market_execution.asyncssh, "connect", fake_connect)
    _, _, run = create_market_run(client, admin_headers, monkeypatch)

    async def changed_details(_resource, filenames, **_kwargs):
        details = await valid_script_details(_resource, filenames)
        details[1]["checksum"] = "f" * 64
        return details

    monkeypatch.setattr(market_script_service, "read_many", changed_details)
    failed = start_workflow_and_step(client, admin_headers, run["id"])
    assert failed["status"] == "awaiting_step_retry"
    assert "已发生变化" in failed["steps"][0]["error_message"]
    assert commands == []

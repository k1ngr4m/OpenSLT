from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.database import SessionLocal
from app.models import Resource, ScenarioWorkflowNode
from app.services import workflows
from conftest import create_plan_scenario, create_resource, publish_workflow


def slnic_nodes() -> list[dict]:
    return [
        {
            "node_key": "slnic-start",
            "node_type": "slnic_start_capture",
            "name": "启动 SLNIC",
            "config": {},
        },
        {
            "node_key": "slnic-stop",
            "node_type": "slnic_stop_capture",
            "name": "关闭 SLNIC",
            "config": {},
        },
        {
            "node_key": "slnic-merge",
            "node_type": "slnic_merge_capture",
            "name": "合并 pcapng",
            "config": {},
        },
    ]


def create_parser_resource(client, headers) -> dict:
    response = client.post(
        "/api/v1/resources",
        headers=headers,
        json={
            "name": "Parser-SLNIC",
            "resource_type": "parser",
            "business_code": "fut_mm",
            "host": "127.0.0.1",
            "ssh_port": 22,
            "username": "tester",
            "auth_type": "password",
            "password": "secret",
            "remote_path": "/home/user0/parser",
            "capabilities": {"parser_tool": "soft_dce_speed_analysis_v7"},
            "version_info": "test",
            "notes": "",
            "is_enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_slnic_run(client, headers, nodes=None):
    resource = create_resource(client, headers, "SLNIC-01", resource_type="slnic")
    selected_nodes = nodes or slnic_nodes()
    resource_ids = [resource["id"]]
    required_types = ["slnic"]
    if any(item["node_type"] == "slnic_merge_capture" for item in selected_nodes):
        parser = create_parser_resource(client, headers)
        resource_ids.append(parser["id"])
        required_types.append("parser")
        with SessionLocal() as db:
            db.get(Resource, resource["id"]).remote_path = "/home/user0/slnic"
            db.commit()
    plan, scenario = create_plan_scenario(
        client, headers, required_types=required_types, resource_ids=resource_ids
    )
    publish_workflow(client, headers, scenario, resource_ids, selected_nodes)
    response = client.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "plan_id": plan["id"],
            "scenario_id": scenario["id"],
            "resource_ids": resource_ids,
            "timeout_minutes": 30,
        },
    )
    assert response.status_code == 201, response.text
    return resource, scenario, response.json()


def execute_current_step(client, headers, run_id):
    run = client.get(f"/api/v1/runs/{run_id}", headers=headers).json()
    step = next(item for item in run["steps"] if item["status"] != "succeeded")
    operation = "retry" if step["status"] == "failed" else "start"
    response = client.post(
        f"/api/v1/runs/{run_id}/steps/{step['id']}/{operation}", headers=headers
    )
    assert response.status_code == 200, response.text
    return client.get(f"/api/v1/runs/{run_id}", headers=headers).json()


def complete_current_step(client, headers, run_id):
    run = client.get(f"/api/v1/runs/{run_id}", headers=headers).json()
    step = next(item for item in run["steps"] if item["status"] != "succeeded")
    response = client.post(
        f"/api/v1/runs/{run_id}/steps/{step['id']}/complete", headers=headers
    )
    assert response.status_code == 200, response.text
    return client.get(f"/api/v1/runs/{run_id}", headers=headers).json()


def execute_and_complete_current_step(client, headers, run_id):
    executed = execute_current_step(client, headers, run_id)
    assert executed["status"] == "awaiting_step_completion"
    return complete_current_step(client, headers, run_id)


def test_slnic_publish_allows_any_node_type_sequence(client, admin_headers):
    resource = create_resource(client, admin_headers, "SLNIC-order", resource_type="slnic")
    parser = create_parser_resource(client, admin_headers)
    with SessionLocal() as db:
        db.get(Resource, resource["id"]).remote_path = "/home/user0/slnic"
        db.commit()
    _, scenario = create_plan_scenario(
        client,
        admin_headers,
        required_types=["slnic", "parser"],
        resource_ids=[resource["id"], parser["id"]],
    )
    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers
    ).json()
    response = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": [resource["id"], parser["id"]],
            "nodes": [
                slnic_nodes()[1],
                slnic_nodes()[2],
                slnic_nodes()[0],
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["validation_errors"] == []
    published = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers
    )
    assert published.status_code == 200, published.text


@pytest.mark.parametrize("node_index", [0, 1, 2])
def test_empty_slnic_commands_can_be_saved_but_not_published(
    client, admin_headers, node_index
):
    resource = create_resource(client, admin_headers, "SLNIC-empty", resource_type="slnic")
    resource_ids = [resource["id"]]
    required_types = ["slnic"]
    if node_index == 2:
        parser = create_parser_resource(client, admin_headers)
        resource_ids.append(parser["id"])
        required_types.append("parser")
        with SessionLocal() as db:
            db.get(Resource, resource["id"]).remote_path = "/home/user0/slnic"
            db.commit()
    _, scenario = create_plan_scenario(
        client, admin_headers, required_types=required_types, resource_ids=resource_ids
    )
    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers
    ).json()
    node = slnic_nodes()[node_index]
    node["config"] = {"commands": [" ", "\n"]}
    saved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": resource_ids,
            "nodes": [node],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["draft"]["nodes"][0]["config"]["commands"] == []
    assert {item["field"] for item in saved.json()["validation_errors"]} == {"commands"}
    published = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers
    )
    assert published.status_code == 422


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("is_enabled", False, "场景资源池缺少已启用的 SLNIC 资源"),
        ("remote_path", "", "SLNIC 资源未配置远端路径"),
    ],
)
def test_slnic_publish_rechecks_resource_state(
    client, admin_headers, field, value, message
):
    resource = create_resource(client, admin_headers, f"SLNIC-{field}", resource_type="slnic")
    _, scenario = create_plan_scenario(
        client, admin_headers, required_types=["slnic"], resource_ids=[resource["id"]]
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
            "nodes": [slnic_nodes()[0]],
        },
    )
    assert saved.status_code == 200, saved.text

    with SessionLocal() as db:
        stored = db.get(Resource, resource["id"])
        setattr(stored, field, value)
        db.commit()

    published = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers
    )
    assert published.status_code == 422, published.text
    assert message in {item["message"] for item in published.json()["errors"]}


def test_remote_slnic_run_executes_configured_commands_and_downloads(
    client, admin_headers, monkeypatch
):
    commands = []
    connections = []
    connect_options = []

    class FakeSFTP:
        def __init__(self):
            self.closed = False

        async def get(self, remote_path, local_path):
            assert remote_path == "/home/user0/parser/merge_pcap.pcapng"
            Path(local_path).write_bytes(b"remote-pcapng")

        async def makedirs(self, _path, exist_ok=False):
            assert exist_ok is True

        async def posix_rename(self, _source, _target):
            raise FileNotFoundError("no previous output")

        def exit(self):
            self.closed = True

    class FakeConnection:
        def __init__(self):
            self.closed = False
            self.sftp = None

        async def run(self, command, check=False):
            assert check is False
            commands.append(command)
            return SimpleNamespace(exit_status=0, stdout="", stderr="")

        async def start_sftp_client(self):
            self.sftp = FakeSFTP()
            return self.sftp

        def close(self):
            self.closed = True

        async def wait_closed(self):
            return None

    async def fake_connect(**kwargs):
        connect_options.append(kwargs)
        connection = FakeConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(workflows.asyncssh, "connect", fake_connect)
    nodes = slnic_nodes()
    nodes[0]["config"] = {
        "commands": ["export MODE=shared", "printf '%s' \"$MODE\""],
    }
    resource, _, run = create_slnic_run(client, admin_headers, nodes)
    with SessionLocal() as db:
        workflow_node = db.get(ScenarioWorkflowNode, run["steps"][0]["workflow_node_id"])
        workflow_node.config = {"commands": ["printf mutated"]}
        db.commit()
    started = client.post(f"/api/v1/runs/{run['id']}/start", headers=admin_headers)
    assert started.status_code == 200, started.text
    completed = None
    for _ in range(3):
        completed = execute_and_complete_current_step(client, admin_headers, run["id"])
    assert completed["status"] == "completed"
    assert [step["node_type"] for step in completed["steps"]] == [
        "slnic_start_capture",
        "slnic_stop_capture",
        "slnic_merge_capture",
    ]
    assert all(step["result_summary"]["resource_id"] == resource["id"] for step in completed["steps"])
    assert len(connections) == 5
    assert all(connection.closed for connection in connections)
    assert connections[-1].sftp.closed is True
    assert all(options["password"] == "secret" for options in connect_options)
    assert len(commands) == 3
    assert commands[0].startswith("cd /home/user0/slnic/tcpdump && /bin/sh -c ")
    assert "export MODE=shared" in commands[0]
    assert "printf" in commands[0]
    assert "mutated" not in commands[0]
    assert commands[0].index("export MODE=shared") < commands[0].index("printf")
    assert "./stop_slnic_dump.sh" in commands[1]
    assert "./pcap_merge_tool slnic*" in commands[2]
    assert "./editcap" not in commands[2]
    merge = completed["steps"][-1]["result_summary"]
    assert merge["size"] == len(b"remote-pcapng")
    assert "mode" not in merge
    assert len(completed["artifacts"]) == 1
    artifact = completed["artifacts"][0]
    assert artifact["artifact_type"] == "packet_capture"
    downloaded = client.get(
        f"/api/v1/artifacts/{artifact['id']}/download", headers=admin_headers
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"remote-pcapng"


def test_remote_slnic_command_failure_waits_for_step_retry(client, admin_headers, monkeypatch):
    class FailedConnection:
        async def run(self, command, check=False):
            return SimpleNamespace(exit_status=7, stdout="", stderr="permission denied")

        def close(self):
            return None

        async def wait_closed(self):
            return None

    async def fake_connect(**kwargs):
        return FailedConnection()

    monkeypatch.setattr(workflows.asyncssh, "connect", fake_connect)
    _, _, run = create_slnic_run(client, admin_headers)
    started = client.post(f"/api/v1/runs/{run['id']}/start", headers=admin_headers)
    assert started.status_code == 200, started.text
    failed = execute_current_step(client, admin_headers, run["id"])
    assert failed["status"] == "awaiting_step_retry"
    assert failed["error_code"] is None
    assert failed["error_message"] is None
    assert failed["steps"][0]["status"] == "failed"
    assert "退出码 7" in failed["steps"][0]["error_message"]
    assert failed["artifacts"] == []


def test_non_terminal_slnic_shell_shares_state_and_stops_on_failure(tmp_path):
    script = workflows._failure_stopping_shell([
        "export OPENSLT_MODE=shared",
        "mkdir state && cd state",
        "printf '%s' \"$OPENSLT_MODE\" > value.txt",
        "false",
        "printf after > after.txt",
    ])
    completed = subprocess.run(
        ["/bin/sh", "-c", script],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert (tmp_path / "state" / "value.txt").read_text() == "shared"
    assert not (tmp_path / "state" / "after.txt").exists()


def test_non_terminal_slnic_retry_replays_the_complete_snapshot(
    client, admin_headers, monkeypatch
):
    dispatched = []
    attempts = 0

    class RetryConnection:
        async def run(self, command, check=False):
            nonlocal attempts
            assert check is False
            attempts += 1
            dispatched.append(command)
            if attempts == 1:
                return SimpleNamespace(exit_status=7, stdout="", stderr="temporary failure")
            return SimpleNamespace(exit_status=0, stdout="", stderr="")

        def close(self):
            return None

        async def wait_closed(self):
            return None

    connection = RetryConnection()

    async def fake_connect(**_kwargs):
        return connection

    commands = ["export MODE=retry", "cd state", "printf '%s' \"$MODE\""]
    node = slnic_nodes()[0]
    node["config"] = {"commands": commands}
    monkeypatch.setattr(workflows.asyncssh, "connect", fake_connect)
    _, _, run = create_slnic_run(client, admin_headers, [node])
    started = client.post(f"/api/v1/runs/{run['id']}/start", headers=admin_headers)
    assert started.status_code == 200, started.text

    failed = execute_current_step(client, admin_headers, run["id"])
    assert failed["status"] == "awaiting_step_retry"
    retried = execute_current_step(client, admin_headers, run["id"])

    assert retried["status"] == "awaiting_step_completion"
    assert len(dispatched) == 2
    assert dispatched[0] == dispatched[1]
    expected_script = workflows._failure_stopping_shell(commands)
    assert dispatched[0] == (
        f"cd /tmp/openslt/tcpdump && /bin/sh -c {shlex.quote(expected_script)}"
    )
    assert retried["steps"][0]["config_snapshot"]["commands"] == commands
    assert retried["steps"][0]["result_summary"]["commands"] == commands
    assert retried["steps"][0]["retry_count"] == 1


def test_remote_slnic_stop_failure_continues_to_merge(client, admin_headers, monkeypatch):
    commands = []
    stop_attempts = 0

    class FakeSFTP:
        async def get(self, remote_path, local_path):
            assert remote_path == "/home/user0/parser/merge_pcap.pcapng"
            Path(local_path).write_bytes(b"remote-pcapng")

        async def makedirs(self, _path, exist_ok=False):
            assert exist_ok is True

        async def posix_rename(self, _source, _target):
            raise FileNotFoundError("no previous output")

        def exit(self):
            return None

    class FakeConnection:
        async def run(self, command, check=False):
            nonlocal stop_attempts
            assert check is False
            commands.append(command)
            if "./stop_slnic_dump.sh" in command:
                stop_attempts += 1
                if stop_attempts == 1:
                    return SimpleNamespace(
                        exit_status=1,
                        stdout="",
                        stderr="window not found: slnic:2_slnic",
                    )
            return SimpleNamespace(exit_status=0, stdout="", stderr="")

        async def start_sftp_client(self):
            return FakeSFTP()

        def close(self):
            return None

        async def wait_closed(self):
            return None

    async def fake_connect(**kwargs):
        return FakeConnection()

    monkeypatch.setattr(workflows.asyncssh, "connect", fake_connect)
    _, _, run = create_slnic_run(client, admin_headers)

    started = client.post(f"/api/v1/runs/{run['id']}/start", headers=admin_headers)

    assert started.status_code == 200, started.text
    execute_and_complete_current_step(client, admin_headers, run["id"])
    failed = execute_current_step(client, admin_headers, run["id"])
    assert failed["status"] == "awaiting_step_retry"
    assert failed["steps"][1]["status"] == "failed"
    assert failed["steps"][2]["status"] == "pending"
    assert "window not found" in failed["steps"][1]["error_message"]

    retried = execute_current_step(client, admin_headers, run["id"])
    assert retried["status"] == "awaiting_step_completion"
    assert retried["steps"][1]["status"] == "waiting"
    assert retried["steps"][1]["retry_count"] == 1
    complete_current_step(client, admin_headers, run["id"])
    completed = execute_and_complete_current_step(client, admin_headers, run["id"])
    assert completed["status"] == "completed"
    assert completed["error_code"] is None
    assert completed["error_message"] is None
    assert completed["steps"][2]["status"] == "succeeded"
    assert any("./pcap_merge_tool slnic*" in command for command in commands)
    assert completed["artifacts"][0]["name"] == "merge_pcap.pcapng"

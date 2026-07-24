from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services import workflows
from conftest import create_plan_scenario, create_resource, publish_workflow


def slnic_nodes() -> list[dict]:
    return [
        {
            "node_key": "slnic-start",
            "node_type": "slnic_start_capture",
            "name": "启动 SLNIC 节点",
            "config": {},
        },
        {
            "node_key": "slnic-stop",
            "node_type": "slnic_stop_capture",
            "name": "关闭 SLNIC 节点",
            "config": {},
        },
        {
            "node_key": "slnic-merge",
            "node_type": "slnic_merge_capture",
            "name": "合并 pcapng",
            "config": {},
        },
    ]


def create_slnic_run(client, headers):
    resource = create_resource(client, headers, "SLNIC-01", resource_type="slnic")
    plan, scenario = create_plan_scenario(
        client, headers, required_types=["slnic"], resource_ids=[resource["id"]]
    )
    publish_workflow(client, headers, scenario, [resource["id"]], slnic_nodes())
    response = client.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "plan_id": plan["id"],
            "scenario_id": scenario["id"],
            "resource_ids": [resource["id"]],
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


def test_slnic_publish_rejects_invalid_sequence(client, admin_headers):
    resource = create_resource(client, admin_headers, "SLNIC-order", resource_type="slnic")
    _, scenario = create_plan_scenario(
        client, admin_headers, required_types=["slnic"], resource_ids=[resource["id"]]
    )
    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers
    ).json()
    response = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": [resource["id"]],
            "nodes": [slnic_nodes()[1], slnic_nodes()[2]],
        },
    )
    assert response.status_code == 200, response.text
    messages = {item["message"] for item in response.json()["validation_errors"]}
    assert "关闭 SLNIC 节点前需要先启动抓包" in messages
    assert "合并 pcapng 前需要先关闭 SLNIC 抓包" in messages
    published = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers
    )
    assert published.status_code == 422
    assert published.json()["code"] == "WORKFLOW_VALIDATION_FAILED"


def test_simulated_slnic_run_creates_downloadable_artifact(
    client, admin_headers, monkeypatch
):
    async def unexpected_connect(**kwargs):
        raise AssertionError(f"simulated mode attempted SSH: {kwargs}")

    monkeypatch.setattr(workflows.asyncssh, "connect", unexpected_connect)
    resource, _, run = create_slnic_run(client, admin_headers)
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
    assert all(step["result_summary"]["mode"] == "simulated" for step in completed["steps"])
    merge = completed["steps"][-1]["result_summary"]
    assert merge["filename"] == "merge_pcap.pcapng"
    assert merge["size"] == 28
    assert len(merge["checksum"]) == 64
    assert len(completed["artifacts"]) == 1
    artifact = completed["artifacts"][0]
    assert artifact["artifact_type"] == "packet_capture"
    downloaded = client.get(
        f"/api/v1/artifacts/{artifact['id']}/download", headers=admin_headers
    )
    assert downloaded.status_code == 200
    assert downloaded.content[:4] == bytes.fromhex("0a0d0d0a")


def test_remote_slnic_run_executes_fixed_commands_and_downloads(
    client, admin_headers, monkeypatch
):
    commands = []
    connections = []
    connect_options = []

    class FakeSFTP:
        def __init__(self):
            self.closed = False

        async def get(self, remote_path, local_path):
            assert remote_path == "/tmp/openslt/tcpdump/merge_pcap.pcapng"
            Path(local_path).write_bytes(b"remote-pcapng")

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

    monkeypatch.setattr(workflows.settings, "execution_mode", "remote")
    monkeypatch.setattr(workflows.asyncssh, "connect", fake_connect)
    _, _, run = create_slnic_run(client, admin_headers)
    started = client.post(f"/api/v1/runs/{run['id']}/start", headers=admin_headers)
    assert started.status_code == 200, started.text
    completed = None
    for _ in range(3):
        completed = execute_and_complete_current_step(client, admin_headers, run["id"])
    assert completed["status"] == "completed"
    assert len(connections) == 3
    assert all(connection.closed for connection in connections)
    assert connections[-1].sftp.closed is True
    assert all(options["password"] == "secret" for options in connect_options)
    assert commands[0] == "cd /tmp/openslt/tcpdump && ./start_slnic_dump.sh"
    assert commands[1] == "cd /tmp/openslt/tcpdump && ./stop_slnic_dump.sh"
    assert commands[2] == "cd /tmp/openslt/tcpdump && ./pcap_mergetoo slnic*"
    assert "merge_pacp.pcap" in commands[3]
    assert commands[4].endswith(
        "./editcap merge_pcap.pcap merge_pcap.pcapng && test -f merge_pcap.pcapng"
    )
    merge = completed["steps"][-1]["result_summary"]
    assert merge["mode"] == "remote"
    assert merge["size"] == len(b"remote-pcapng")


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

    monkeypatch.setattr(workflows.settings, "execution_mode", "remote")
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


def test_remote_slnic_stop_failure_continues_to_merge(client, admin_headers, monkeypatch):
    commands = []
    stop_attempts = 0

    class FakeSFTP:
        async def get(self, remote_path, local_path):
            assert remote_path == "/tmp/openslt/tcpdump/merge_pcap.pcapng"
            Path(local_path).write_bytes(b"remote-pcapng")

        def exit(self):
            return None

    class FakeConnection:
        async def run(self, command, check=False):
            nonlocal stop_attempts
            assert check is False
            commands.append(command)
            if command.endswith("./stop_slnic_dump.sh"):
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

    monkeypatch.setattr(workflows.settings, "execution_mode", "remote")
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
    assert any("./pcap_mergetoo slnic*" in command for command in commands)
    assert completed["artifacts"][0]["name"] == "merge_pcap.pcapng"

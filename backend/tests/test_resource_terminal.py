from __future__ import annotations

import typing
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.routes import runs as runs_route
from app.core.database import SessionLocal
from app.models import AuditLog, Resource, ScenarioWorkflowNode, TestRun as RunModel
from app.services import order_configs
from app.services import terminal as terminal_service
from app.services import workflow_contracts, workflows
from app.services.market_scripts import market_script_service
from app.services.run_state import transition_run, transition_step
from app.services.workflows import WorkflowError
from app.workflow_node_configs import PARSER_ACTIONS
from conftest import create_plan_scenario, create_resource, publish_workflow


def access_token(headers: typing.Dict[str, str]) -> str:
    return headers["Authorization"][len("Bearer ") :]


def terminal_url(resource_id: int, token: str) -> str:
    return f"/api/v1/ws/resources/{resource_id}/terminal?token={token}"


def test_parser_terminal_actions_default_and_resource_override():
    assert terminal_service.parser_actions_for_resource(SimpleNamespace(capabilities={})) == PARSER_ACTIONS
    assert terminal_service.parser_actions_for_resource(
        SimpleNamespace(capabilities={"parser_actions": [PARSER_ACTIONS[1], PARSER_ACTIONS[0]]})
    ) == (PARSER_ACTIONS[1], PARSER_ACTIONS[0])


def test_parser_start_command_uses_isolated_workdir_and_xml_filename():
    assert terminal_service.build_parser_start_command(
        "/home/user0/soft_dce_speed_analysis_v7/.openslt-runs/r1-s2-a0-abcd1234",
        "/home/user0/soft_dce_speed_analysis_v7/soft_dce_speed_analysis_v7",
        "soft_dce_speed_analysis.xml",
    ) == (
        "cd /home/user0/soft_dce_speed_analysis_v7/.openslt-runs/r1-s2-a0-abcd1234 && "
        "/home/user0/soft_dce_speed_analysis_v7/soft_dce_speed_analysis_v7 "
        "soft_dce_speed_analysis.xml"
    )


def dispatch_terminal_step(
    client: TestClient,
    resource_id: int,
    token: str,
    run_id: int,
    step_id: int,
    operation: str = "start",
) -> dict:
    with client.websocket_connect(terminal_url(resource_id, token)) as websocket:
        assert websocket.receive_json()["status"] == "connecting"
        assert websocket.receive_json()["status"] == "connected"
        assert "remote-ready" in websocket.receive_json()["data"]
        websocket.send_json(
            {
                "type": "workflow_step_command",
                "run_id": run_id,
                "step_id": step_id,
                "operation": operation,
            }
        )
        return websocket.receive_json()


def slnic_start_nodes(commands: typing.Optional[typing.List[str]] = None) -> list[dict]:
    return [
        {
            "node_key": "slnic-start",
            "node_type": "slnic_start_capture",
            "name": "启动 SLNIC",
            "config": {} if commands is None else {"commands": commands},
        }
    ]


def rem_start_nodes(commands: typing.Optional[typing.List[str]] = None) -> list[dict]:
    return [
        {
            "node_key": "rem-start",
            "node_type": "rem_startup",
            "name": "启动 REM 柜台",
            "config": {} if commands is None else {"commands": commands},
        }
    ]


MARKET_SCRIPTS = [
    {"filename": "prepare.sh", "checksum": "a" * 64},
    {"filename": "start_all.sh", "checksum": "b" * 64},
]


def market_start_nodes() -> list[dict]:
    return [
        {
            "node_key": "market-start",
            "node_type": "market_startup",
            "name": "启动模拟市场",
            "config": {"scripts": MARKET_SCRIPTS},
        }
    ]


def slnic_start_stop_nodes() -> list[dict]:
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
    ]


def slnic_start_stop_merge_nodes() -> list[dict]:
    return [
        *slnic_start_stop_nodes(),
        {
            "node_key": "slnic-merge",
            "node_type": "slnic_merge_capture",
            "name": "合并 pcapng",
            "config": {},
        },
    ]


def order_start_nodes() -> list[dict]:
    return [
        {
            "node_key": "order-start",
            "node_type": "order_preparation",
            "name": "发单准备",
            "config": {"xml_filename": "order.xml", "network_interface": "p4p1", "read_symbol_csv": 0},
        }
    ]


ORDER_XML = '''<?xml version="1.0" encoding="utf-8"?>
<tcp>
  <group_new_order id="new_order" disp="NEW_ORDER"><price disp="PRICE" value="1495.0000" /></group_new_order>
  <read_symbol_csv value="0" />
</tcp>'''


def create_slnic_start_run(
    client: TestClient,
    headers: typing.Dict[str, str],
    commands: typing.Optional[typing.List[str]] = None,
) -> tuple[dict, dict]:
    resource = create_resource(client, headers, "SLNIC-Terminal", resource_type="slnic")
    plan, scenario = create_plan_scenario(client, headers, required_types=["slnic"], resource_ids=[resource["id"]])
    publish_workflow(client, headers, scenario, [resource["id"]], slnic_start_nodes(commands))
    created = client.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "plan_id": plan["id"],
            "scenario_id": scenario["id"],
            "resource_ids": [resource["id"]],
            "timeout_minutes": 30,
        },
    )
    assert created.status_code == 201, created.text
    started = client.post(f"/api/v1/runs/{created.json()['id']}/start", headers=headers)
    assert started.status_code == 200, started.text
    return resource, client.get(f"/api/v1/runs/{created.json()['id']}", headers=headers).json()


def create_slnic_merge_run(
    client: TestClient,
    headers: typing.Dict[str, str],
    commands: typing.Optional[typing.List[str]] = None,
) -> tuple[dict, dict]:
    resource = create_resource(client, headers, "SLNIC-Merge-Terminal", resource_type="slnic")
    plan, scenario = create_plan_scenario(
        client, headers, required_types=["slnic"], resource_ids=[resource["id"]]
    )
    node = {
        "node_key": "slnic-merge",
        "node_type": "slnic_merge_capture",
        "name": "合并 pcapng",
        "config": {} if commands is None else {"commands": commands},
    }
    publish_workflow(client, headers, scenario, [resource["id"]], [node])
    created = client.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "plan_id": plan["id"],
            "scenario_id": scenario["id"],
            "resource_ids": [resource["id"]],
            "timeout_minutes": 30,
        },
    )
    assert created.status_code == 201, created.text
    started = client.post(f"/api/v1/runs/{created.json()['id']}/start", headers=headers)
    assert started.status_code == 200, started.text
    return resource, client.get(f"/api/v1/runs/{created.json()['id']}", headers=headers).json()


def create_rem_start_run(
    client: TestClient,
    headers: typing.Dict[str, str],
    commands: typing.Optional[typing.List[str]] = None,
) -> tuple[dict, dict]:
    resource = create_resource(client, headers, "REM-Terminal", resource_type="rem")
    plan, scenario = create_plan_scenario(
        client,
        headers,
        required_types=["rem"],
        resource_ids=[resource["id"]],
    )
    publish_workflow(client, headers, scenario, [resource["id"]], rem_start_nodes(commands))
    created = client.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "plan_id": plan["id"],
            "scenario_id": scenario["id"],
            "resource_ids": [resource["id"]],
            "timeout_minutes": 30,
        },
    )
    assert created.status_code == 201, created.text
    started = client.post(f"/api/v1/runs/{created.json()['id']}/start", headers=headers)
    assert started.status_code == 200, started.text
    return resource, client.get(f"/api/v1/runs/{created.json()['id']}", headers=headers).json()


def create_market_start_run(
    client: TestClient,
    headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict, dict]:
    async def valid_script_details(_resource, filenames, **_kwargs):
        checksums = {item["filename"]: item["checksum"] for item in MARKET_SCRIPTS}
        return [
            {
                "name": filename,
                "checksum": checksums[filename],
                "executable": True,
                "path": f"/tmp/openslt/{filename}",
            }
            for filename in filenames
        ]

    monkeypatch.setattr(market_script_service, "read_many", valid_script_details)
    resource = create_resource(client, headers, "Market-Terminal", resource_type="market")
    plan, scenario = create_plan_scenario(
        client,
        headers,
        required_types=["market"],
        resource_ids=[resource["id"]],
    )
    publish_workflow(client, headers, scenario, [resource["id"]], market_start_nodes())
    created = client.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "plan_id": plan["id"],
            "scenario_id": scenario["id"],
            "resource_ids": [resource["id"]],
            "timeout_minutes": 30,
        },
    )
    assert created.status_code == 201, created.text
    started = client.post(f"/api/v1/runs/{created.json()['id']}/start", headers=headers)
    assert started.status_code == 200, started.text
    return resource, client.get(f"/api/v1/runs/{created.json()['id']}", headers=headers).json()


def create_slnic_start_stop_run(client: TestClient, headers: typing.Dict[str, str]) -> tuple[dict, dict]:
    resource = create_resource(client, headers, "SLNIC-Terminal", resource_type="slnic")
    plan, scenario = create_plan_scenario(client, headers, required_types=["slnic"], resource_ids=[resource["id"]])
    publish_workflow(client, headers, scenario, [resource["id"]], slnic_start_stop_nodes())
    created = client.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "plan_id": plan["id"],
            "scenario_id": scenario["id"],
            "resource_ids": [resource["id"]],
            "timeout_minutes": 30,
        },
    )
    assert created.status_code == 201, created.text
    started = client.post(f"/api/v1/runs/{created.json()['id']}/start", headers=headers)
    assert started.status_code == 200, started.text
    return resource, client.get(f"/api/v1/runs/{created.json()['id']}", headers=headers).json()


def create_slnic_start_stop_merge_run(client: TestClient, headers: typing.Dict[str, str]) -> tuple[dict, dict]:
    resource = create_resource(client, headers, "SLNIC-Terminal", resource_type="slnic")
    plan, scenario = create_plan_scenario(client, headers, required_types=["slnic"], resource_ids=[resource["id"]])
    publish_workflow(client, headers, scenario, [resource["id"]], slnic_start_stop_merge_nodes())
    created = client.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "plan_id": plan["id"],
            "scenario_id": scenario["id"],
            "resource_ids": [resource["id"]],
            "timeout_minutes": 30,
        },
    )
    assert created.status_code == 201, created.text
    started = client.post(f"/api/v1/runs/{created.json()['id']}/start", headers=headers)
    assert started.status_code == 200, started.text
    return resource, client.get(f"/api/v1/runs/{created.json()['id']}", headers=headers).json()


def create_order_start_run(client: TestClient, headers: typing.Dict[str, str], monkeypatch: pytest.MonkeyPatch) -> tuple[dict, dict]:
    async def fake_read_order_config(_resource: Resource, filename: str):
        declaration, document = order_configs.parse_xml(ORDER_XML)
        return {
            "name": filename,
            "size": len(ORDER_XML.encode()),
            "modified_at": None,
            "checksum": order_configs.checksum(ORDER_XML),
            "content": ORDER_XML,
            "declaration": declaration,
            "document": document,
            "tool": "ees_ef_vi_trader_binary_api_test",
        }

    monkeypatch.setattr(workflow_contracts.order_config_service, "read", fake_read_order_config)
    resource = create_resource(client, headers, "Order-Terminal", resource_type="order")
    plan, scenario = create_plan_scenario(client, headers, required_types=["order"], resource_ids=[resource["id"]])
    publish_workflow(client, headers, scenario, [resource["id"]], order_start_nodes())
    created = client.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "plan_id": plan["id"],
            "scenario_id": scenario["id"],
            "resource_ids": [resource["id"]],
            "timeout_minutes": 30,
        },
    )
    assert created.status_code == 201, created.text
    started = client.post(f"/api/v1/runs/{created.json()['id']}/start", headers=headers)
    assert started.status_code == 200, started.text
    return resource, client.get(f"/api/v1/runs/{created.json()['id']}", headers=headers).json()


def test_terminal_rejects_invalid_token_and_visitor(client: TestClient, admin_headers: typing.Dict[str, str]):
    resource = create_resource(client, admin_headers, "REM-Permissions")
    with pytest.raises(WebSocketDisconnect) as invalid:
        with client.websocket_connect(terminal_url(resource["id"], "invalid-token")):
            pass
    assert invalid.value.code == 4401

    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"username": "terminal-viewer", "display_name": "终端访客", "password": "viewer-password", "role": "visitor"},
    )
    assert created.status_code == 201
    login = client.post("/api/v1/auth/login", json={"username": "terminal-viewer", "password": "viewer-password"})
    visitor_token = login.json()["access_token"]
    with pytest.raises(WebSocketDisconnect) as forbidden:
        with client.websocket_connect(terminal_url(resource["id"], visitor_token)):
            pass
    assert forbidden.value.code == 4403


def test_terminal_rejects_unsupported_and_disabled_resources(client: TestClient, admin_headers: typing.Dict[str, str]):
    token = access_token(admin_headers)
    with pytest.raises(WebSocketDisconnect) as missing_close:
        with client.websocket_connect(terminal_url(999_999, token)):
            pass
    assert missing_close.value.code == 4403

    unsupported = create_resource(client, admin_headers, "Capture-01", resource_type="capture")
    with pytest.raises(WebSocketDisconnect) as unsupported_close:
        with client.websocket_connect(terminal_url(unsupported["id"], token)):
            pass
    assert unsupported_close.value.code == 4403

    disabled = create_resource(client, admin_headers, "REM-Disabled")
    db = SessionLocal()
    try:
        row = db.get(Resource, disabled["id"])
        row.is_enabled = False
        db.commit()
    finally:
        db.close()
    with pytest.raises(WebSocketDisconnect) as disabled_close:
        with client.websocket_connect(terminal_url(disabled["id"], token)):
            pass
    assert disabled_close.value.code == 4403


class FakeStdin:
    def __init__(self) -> None:
        self.writes: typing.List[str] = []

    def write(self, data: str) -> None:
        self.writes.append(data)


class FakeStdout:
    def __init__(self) -> None:
        self.first_read = True

    async def read(self, _: int) -> str:
        if self.first_read:
            self.first_read = False
            return "remote-ready\r\n"
        await asyncio.get_running_loop().create_future()


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = FakeStdin()
        self.stdout = FakeStdout()
        self.exit_status = 0
        self.sizes: typing.List[typing.Tuple[int, int]] = []
        self.closed = False

    def change_terminal_size(self, columns: int, rows: int) -> None:
        self.sizes.append((columns, rows))

    async def wait(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class FakeSFTP:
    def __init__(self) -> None:
        self.gets: typing.List[typing.Tuple[str, str]] = []
        self.lstats: typing.List[str] = []
        self.attrs_by_path: typing.Dict[str, object] = {}
        self.exited = False

    async def lstat(self, remote_path: str):
        self.lstats.append(remote_path)
        value = self.attrs_by_path.get(
            remote_path,
            SimpleNamespace(
                type=terminal_service.asyncssh.FILEXFER_TYPE_REGULAR,
                permissions=0o755,
            ),
        )
        if isinstance(value, Exception):
            raise value
        return value

    async def get(self, remote_path: str, local_path: str) -> None:
        self.gets.append((remote_path, local_path))
        Path(local_path).write_bytes(b"merged-pcapng")

    def exit(self) -> None:
        self.exited = True


class FakeConnection:
    def __init__(self) -> None:
        self.process = FakeProcess()
        self.sftp = FakeSFTP()
        self.command: typing.Union[str, None] = None
        self.process_options: dict = {}
        self.closed = False

    async def create_process(self, command: typing.Union[str, None], **options):
        self.command = command
        self.process_options = options
        return self.process

    async def start_sftp_client(self):
        return self.sftp

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def prepare_waiting_parser_lease_run(
    client: TestClient,
    headers: typing.Dict[str, str],
) -> tuple[dict, dict]:
    resource, run = create_slnic_start_run(client, headers)
    with SessionLocal() as db:
        stored_resource = db.get(Resource, resource["id"])
        stored_run = db.get(RunModel, run["id"])
        stored_step = stored_run.steps[0]
        stored_node = db.get(ScenarioWorkflowNode, stored_step.workflow_node_id)
        stored_resource.resource_type = "parser"
        stored_resource.capabilities = {
            "parser_tool": "soft_cffex_speed_analysis_v2",
            "parser_binary": "soft_cffex_speed_analysis_v2",
            "parser_actions": [PARSER_ACTIONS[0]],
        }
        stored_step.node_type = "parser_parse"
        stored_node.node_type = "parser_parse"
        transition_run(stored_run, "awaiting_step_completion")
        transition_step(stored_step, "waiting")
        stored_step.result_summary = {
            "mode": "terminal",
            "remote_workdir": "/tmp/openslt/.openslt-runs/parser-lease-test",
            "input_checksums": {"t_fut_orders.csv": "input-checksum"},
            "supported_parser_actions": [PARSER_ACTIONS[0]],
            "parser_action_history": [],
        }
        db.commit()
    resource["resource_type"] = "parser"
    resource["capabilities"] = {"parser_actions": [PARSER_ACTIONS[0]]}
    run = client.get(f"/api/v1/runs/{run['id']}", headers=headers).json()
    return resource, run


@pytest.mark.asyncio
async def test_parser_action_write_failure_is_not_recorded_as_dispatched(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
):
    resource, run = prepare_waiting_parser_lease_run(client, admin_headers)
    step = run["steps"][0]

    class FakeWebSocket:
        def __init__(self):
            self.messages = [{
                "type": "parser_action",
                "run_id": run["id"],
                "step_id": step["id"],
                "action": PARSER_ACTIONS[0],
            }]
            self.sent: list[dict] = []

        async def receive_json(self):
            if self.messages:
                return self.messages.pop(0)
            raise WebSocketDisconnect()

        async def send_json(self, payload: dict):
            self.sent.append(payload)

    process = FakeProcess()

    def fail_write(_data: str) -> None:
        raise BrokenPipeError("parser input closed")

    process.stdin.write = fail_write
    websocket = FakeWebSocket()
    lease = terminal_service.ParserTerminalLease(run_id=run["id"], step_id=step["id"])
    terminal_resource = terminal_service.TerminalResource(
        id=resource["id"],
        name=resource["name"],
        resource_type="parser",
        host=resource["host"],
        port=resource["ssh_port"],
        username=resource["username"],
        password=None,
        private_key=None,
        remote_path="/tmp/openslt",
        capabilities={"parser_actions": [PARSER_ACTIONS[0]]},
    )

    reason = await terminal_service._receive_remote(
        typing.cast(typing.Any, websocket),
        typing.cast(typing.Any, process),
        typing.cast(typing.Any, FakeConnection()),
        actor_id=1,
        resource=terminal_resource,
        lease=lease,
    )

    assert reason == "client_disconnected"
    assert websocket.sent[0]["type"] == "parser_action"
    assert websocket.sent[0]["code"] == "SSH_COMMAND_DISPATCH_FAILED"
    updated = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert updated["status"] == "awaiting_step_retry"
    assert updated["steps"][0]["status"] == "failed"
    assert updated["steps"][0]["result_summary"]["parser_action_history"] == []


@pytest.mark.asyncio
async def test_parser_action_rejection_uses_parser_action_response_type(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
):
    resource, run = prepare_waiting_parser_lease_run(client, admin_headers)
    step = run["steps"][0]
    terminal_resource = terminal_service.TerminalResource(
        id=resource["id"],
        name=resource["name"],
        resource_type="parser",
        host=resource["host"],
        port=resource["ssh_port"],
        username=resource["username"],
        password=None,
        private_key=None,
        remote_path="/tmp/openslt",
        capabilities={"parser_actions": [PARSER_ACTIONS[0]]},
    )

    with SessionLocal() as db:
        response = await terminal_service._dispatch_parser_action(
            db=db,
            actor_id=1,
            resource=terminal_resource,
            run_id=run["id"],
            step_id=step["id"],
            action=PARSER_ACTIONS[1],
            lease=terminal_service.ParserTerminalLease(run_id=run["id"], step_id=step["id"]),
        )

    assert response["type"] == "parser_action"
    assert response["status"] == "failed"
    assert response["code"] == "PARSER_ACTION_NOT_ALLOWED"


def test_parser_terminal_session_error_fails_active_lease(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    resource, run = prepare_waiting_parser_lease_run(client, admin_headers)
    step = run["steps"][0]

    async def fail_active_session(_websocket, _resource, _actor_id, on_connected, lease):
        on_connected()
        lease.run_id = run["id"]
        lease.step_id = step["id"]
        raise BrokenPipeError("parser input channel closed")

    monkeypatch.setattr(terminal_service, "_run_remote", fail_active_session)

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(terminal_url(resource["id"], access_token(admin_headers))) as websocket:
            assert websocket.receive_json()["status"] == "connecting"
            error = websocket.receive_json()
            assert error["code"] == "SSH_SESSION_FAILED"
            websocket.receive_json()

    updated = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert updated["status"] == "awaiting_step_retry"
    assert updated["steps"][0]["status"] == "failed"
    assert updated["steps"][0]["error_message"] == "解析 SSH 终端已断开，节点需要重试"
    step_retry = client.post(
        f"/api/v1/runs/{run['id']}/steps/{step['id']}/retry",
        headers=admin_headers,
    )
    assert step_retry.status_code == 409
    assert step_retry.json()["code"] == "PARSER_TERMINAL_REQUIRED"
    run_retry = client.post(f"/api/v1/runs/{run['id']}/retry", headers=admin_headers)
    assert run_retry.status_code == 409
    assert run_retry.json()["code"] == "PARSER_TERMINAL_REQUIRED"


@pytest.mark.asyncio
async def test_parser_output_registration_failure_leaves_no_partial_outputs(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    resource, run = prepare_waiting_parser_lease_run(client, admin_headers)

    class OutputSFTP:
        def scandir(self, _directory):
            async def entries():
                for filename in ("first.csv", "second.csv"):
                    yield SimpleNamespace(
                        filename=filename,
                        attrs=SimpleNamespace(
                            type=workflows.asyncssh.FILEXFER_TYPE_REGULAR,
                            size=4,
                            mtime=1,
                        ),
                    )
            return entries()

        async def get(self, remote_path: str, local_path: str):
            Path(local_path).write_text(Path(remote_path).name, encoding="utf-8")

        def exit(self):
            return None

    class OutputConnection:
        def __init__(self):
            self.sftp = OutputSFTP()

        async def start_sftp_client(self):
            return self.sftp

        def close(self):
            return None

        async def wait_closed(self):
            return None

    async def fake_connect(**_options):
        return OutputConnection()

    registrations = 0
    original_register = workflows._register_parser_artifact

    def fail_second_registration(db, stored_run, stored_step, target):
        nonlocal registrations
        registrations += 1
        if registrations == 2:
            raise OSError("artifact registry unavailable")
        return original_register(db, stored_run, stored_step, target)

    monkeypatch.setattr(workflows.asyncssh, "connect", fake_connect)
    monkeypatch.setattr(workflows, "_register_parser_artifact", fail_second_registration)

    with SessionLocal() as db:
        stored_run = db.get(RunModel, run["id"])
        stored_step = stored_run.steps[0]
        stored_resource = db.get(Resource, resource["id"])
        artifact_directory = workflows._parser_artifact_directory(stored_run, stored_step)
        with pytest.raises(WorkflowError) as failed:
            await workflows.collect_parser_outputs(db, stored_run, stored_step, stored_resource)
        db.rollback()

    assert failed.value.code == "PARSER_OUTPUT_DOWNLOAD_FAILED"
    assert list(artifact_directory.glob("*.csv")) == []
    assert list(artifact_directory.glob("*.part")) == []


@pytest.mark.asyncio
async def test_parser_output_download_failure_removes_incomplete_temp_file(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    resource, run = prepare_waiting_parser_lease_run(client, admin_headers)

    class FailingSFTP:
        def scandir(self, _directory):
            async def entries():
                yield SimpleNamespace(
                    filename="broken.csv",
                    attrs=SimpleNamespace(
                        type=workflows.asyncssh.FILEXFER_TYPE_REGULAR,
                        size=4,
                        mtime=1,
                    ),
                )
            return entries()

        async def get(self, _remote_path: str, local_path: str):
            Path(local_path).write_text("partial", encoding="utf-8")
            raise OSError("download interrupted")

        def exit(self):
            return None

    class FailingConnection:
        async def start_sftp_client(self):
            return FailingSFTP()

        def close(self):
            return None

        async def wait_closed(self):
            return None

    async def fake_connect(**_options):
        return FailingConnection()

    monkeypatch.setattr(workflows.asyncssh, "connect", fake_connect)

    with SessionLocal() as db:
        stored_run = db.get(RunModel, run["id"])
        stored_step = stored_run.steps[0]
        stored_resource = db.get(Resource, resource["id"])
        artifact_directory = workflows._parser_artifact_directory(stored_run, stored_step)
        with pytest.raises(WorkflowError) as failed:
            await workflows.collect_parser_outputs(db, stored_run, stored_step, stored_resource)

    assert failed.value.code == "PARSER_OUTPUT_DOWNLOAD_FAILED"
    assert list(artifact_directory.glob("*.csv")) == []
    assert list(artifact_directory.glob("*.part")) == []


def test_remote_terminal_uses_pty_and_forwards_io(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    resource = create_resource(client, admin_headers, "REM-Remote")
    token = access_token(admin_headers)
    connection = FakeConnection()
    connect_options: dict = {}

    async def fake_connect(**options):
        connect_options.update(options)
        return connection

    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)

    with client.websocket_connect(terminal_url(resource["id"], token)) as websocket:
        connecting = websocket.receive_json()
        assert connecting == {
            "type": "status",
            "status": "connecting",
            "message": "正在建立终端会话",
        }
        connected = websocket.receive_json()
        assert connected == {"type": "status", "status": "connected", "message": "SSH 已连接"}
        assert "remote-ready" in websocket.receive_json()["data"]
        websocket.send_json({"type": "resize", "cols": 180, "rows": 52})
        websocket.send_json({"type": "input", "data": "echo ready\r"})

    assert connect_options["host"] == "127.0.0.1"
    assert connect_options["username"] == "tester"
    assert connect_options["password"] == "secret"
    assert connection.process_options["term_type"] == "xterm-256color"
    assert connection.process_options["term_size"] == (120, 32)
    assert "cd -- /tmp/openslt" in (connection.command or "")
    assert connection.process.stdin.writes == ["echo ready\r"]
    assert connection.process.sizes == [(180, 52)]
    db = SessionLocal()
    try:
        audits = list(
            db.query(AuditLog)
            .filter(AuditLog.object_id == str(resource["id"]), AuditLog.action.like("resource.terminal.%"))
            .order_by(AuditLog.id)
        )
        assert audits[0].action == "resource.terminal.open"
        assert audits[0].detail == {}
        assert "echo ready" not in str([item.detail for item in audits])
    finally:
        db.close()


def test_terminal_workflow_command_dispatches_slnic_start_and_waits_for_completion(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    resource, run = create_slnic_start_run(client, admin_headers)
    step = run["steps"][0]
    token = access_token(admin_headers)
    connection = FakeConnection()

    async def fake_connect(**_):
        return connection

    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)

    with client.websocket_connect(terminal_url(resource["id"], token)) as websocket:
        assert websocket.receive_json()["status"] == "connecting"
        assert websocket.receive_json()["status"] == "connected"
        assert "remote-ready" in websocket.receive_json()["data"]
        websocket.send_json(
            {
                "type": "workflow_step_command",
                "run_id": run["id"],
                "step_id": step["id"],
                "operation": "start",
            }
        )
        response = websocket.receive_json()
        assert response["type"] == "workflow_command"
        assert response["status"] == "dispatched"
        expected = terminal_service._build_terminal_command(
            "/tmp/openslt/tcpdump", ["./start_slnic_dump.sh"]
        )
        assert response["command"] == expected
        assert response["commands"] == ["./start_slnic_dump.sh"]

    assert connection.process.stdin.writes == [expected + "\r"]
    updated = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert updated["status"] == "awaiting_step_completion"
    updated_step = updated["steps"][0]
    assert updated_step["status"] == "waiting"
    assert updated_step["progress"] == 100
    assert updated_step["result_summary"]["resource_id"] == resource["id"]
    assert updated_step["result_summary"]["resource_name"] == resource["name"]
    assert updated_step["result_summary"]["mode"] == "terminal"
    assert updated_step["result_summary"]["exit_code"] is None


def test_terminal_slnic_uses_run_snapshot_sends_every_line_and_retries_all(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    commands = [
        "export MODE=terminal",
        "cd nested",
        "false",
        "if true; then printf '%s' \"$MODE\"; fi",
    ]
    resource, run = create_slnic_start_run(client, admin_headers, commands)
    step = run["steps"][0]
    token = access_token(admin_headers)
    connections: typing.List[FakeConnection] = []

    with SessionLocal() as db:
        workflow_node = db.get(ScenarioWorkflowNode, step["workflow_node_id"])
        workflow_node.config = {"commands": ["printf mutated"]}
        db.commit()

    async def fake_connect(**_):
        connection = FakeConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)

    def dispatch(operation: str) -> dict:
        with client.websocket_connect(terminal_url(resource["id"], token)) as websocket:
            websocket.receive_json()
            websocket.receive_json()
            websocket.receive_json()
            websocket.send_json({
                "type": "workflow_step_command",
                "run_id": run["id"],
                "step_id": step["id"],
                "operation": operation,
            })
            return websocket.receive_json()

    started = dispatch("start")
    expected = terminal_service._build_terminal_command("/tmp/openslt/tcpdump", commands)
    assert started["command"] == expected
    assert started["commands"] == commands
    assert connections[0].process.stdin.writes == [expected + "\r"]
    assert "printf mutated" not in expected
    assert expected.index("false") < expected.index("if true")
    assert "openslt_slnic_status" not in expected

    with SessionLocal() as db:
        stored = db.get(RunModel, run["id"])
        transition_run(stored, "awaiting_step_retry")
        transition_step(stored.steps[0], "failed")
        db.commit()

    retried = dispatch("retry")
    assert retried["command"] == expected
    assert connections[1].process.stdin.writes == [expected + "\r"]
    updated = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert updated["steps"][0]["retry_count"] == 1
    assert updated["steps"][0]["result_summary"]["commands"] == commands


def test_terminal_workflow_command_dispatches_rem_snapshot_in_shared_shell(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    commands = ["export MODE=terminal", "false", "printf '%s' \"$MODE\""]
    resource, run = create_rem_start_run(client, admin_headers, commands)
    step = run["steps"][0]
    token = access_token(admin_headers)
    connection = FakeConnection()

    async def fake_connect(**_):
        return connection

    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)

    with client.websocket_connect(terminal_url(resource["id"], token)) as websocket:
        websocket.receive_json()
        websocket.receive_json()
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "workflow_step_command",
                "run_id": run["id"],
                "step_id": step["id"],
                "operation": "start",
            }
        )
        response = websocket.receive_json()

    expected = terminal_service._build_terminal_command("/tmp/openslt", commands)
    assert response["status"] == "dispatched"
    assert response["command"] == expected
    assert response["commands"] == commands
    assert connection.process.stdin.writes == [expected + "\r"]
    assert expected.index("false") < expected.index("printf")
    updated = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert updated["status"] == "awaiting_step_completion"
    summary = updated["steps"][0]["result_summary"]
    assert summary["mode"] == "terminal"
    assert summary["remote_workdir"] == "/tmp/openslt"
    assert summary["commands"] == commands
    assert summary["exit_code"] is None


def test_terminal_workflow_command_dispatches_market_snapshot_in_order(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    resource, run = create_market_start_run(client, admin_headers, monkeypatch)
    step = run["steps"][0]
    connection = FakeConnection()

    with SessionLocal() as db:
        workflow_node = db.get(ScenarioWorkflowNode, step["workflow_node_id"])
        workflow_node.config = {
            "scripts": [{"filename": "mutated.sh", "checksum": "c" * 64}]
        }
        db.commit()

    async def fake_connect(**_):
        return connection

    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)
    response = dispatch_terminal_step(
        client,
        resource["id"],
        access_token(admin_headers),
        run["id"],
        step["id"],
    )

    commands = ["./prepare.sh", "./start_all.sh"]
    expected = terminal_service._build_chained_terminal_command("/tmp/openslt", commands)
    assert response["status"] == "dispatched"
    assert response["command"] == expected
    assert response["commands"] == commands
    assert response["scripts"] == ["prepare.sh", "start_all.sh"]
    assert "./prepare.sh &&\n./start_all.sh" in expected
    assert connection.process.stdin.writes == [expected + "\r"]
    assert connection.sftp.lstats == [
        "/tmp/openslt/prepare.sh",
        "/tmp/openslt/start_all.sh",
    ]
    assert connection.sftp.exited is True

    updated = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    summary = updated["steps"][0]["result_summary"]
    assert updated["status"] == "awaiting_step_completion"
    assert summary["mode"] == "terminal"
    assert summary["scripts"] == ["prepare.sh", "start_all.sh"]
    assert summary["commands"] == commands
    assert summary["exit_code"] is None


@pytest.mark.parametrize(
    ("script_state", "error_code"),
    [
        (FileNotFoundError("missing"), "MARKET_SCRIPT_NOT_FOUND"),
        (
            SimpleNamespace(
                type=terminal_service.asyncssh.FILEXFER_TYPE_SYMLINK,
                permissions=0o755,
            ),
            "MARKET_SCRIPT_INVALID",
        ),
        (
            SimpleNamespace(
                type=terminal_service.asyncssh.FILEXFER_TYPE_REGULAR,
                permissions=0o644,
            ),
            "MARKET_SCRIPT_NOT_EXECUTABLE",
        ),
    ],
)
def test_terminal_market_preflight_rejects_invalid_script_metadata(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    script_state: object,
    error_code: str,
):
    resource, run = create_market_start_run(client, admin_headers, monkeypatch)
    step = run["steps"][0]
    connection = FakeConnection()
    connection.sftp.attrs_by_path["/tmp/openslt/prepare.sh"] = script_state

    async def fake_connect(**_):
        return connection

    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)
    response = dispatch_terminal_step(
        client,
        resource["id"],
        access_token(admin_headers),
        run["id"],
        step["id"],
    )

    assert response["status"] == "failed"
    assert response["code"] == error_code
    assert connection.process.stdin.writes == []
    assert connection.sftp.exited is True
    unchanged = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert unchanged["status"] == "awaiting_step_start"
    assert unchanged["steps"][0]["status"] == "pending"


def test_terminal_market_rejects_resource_outside_run(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    _resource, run = create_market_start_run(client, admin_headers, monkeypatch)
    other = create_resource(client, admin_headers, "Market-Other", resource_type="market")
    connection = FakeConnection()

    async def fake_connect(**_):
        return connection

    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)
    response = dispatch_terminal_step(
        client,
        other["id"],
        access_token(admin_headers),
        run["id"],
        run["steps"][0]["id"],
    )

    assert response["status"] == "failed"
    assert response["code"] == "INVALID_RESOURCE"
    assert connection.sftp.lstats == []
    assert connection.process.stdin.writes == []


def test_terminal_market_retry_revalidates_and_dispatches_all_scripts(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    resource, run = create_market_start_run(client, admin_headers, monkeypatch)
    step = run["steps"][0]
    connections: typing.List[FakeConnection] = []

    async def fake_connect(**_):
        connection = FakeConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)
    started = dispatch_terminal_step(
        client,
        resource["id"],
        access_token(admin_headers),
        run["id"],
        step["id"],
    )
    assert started["status"] == "dispatched"

    with SessionLocal() as db:
        stored = db.get(RunModel, run["id"])
        transition_run(stored, "awaiting_step_retry")
        transition_step(stored.steps[0], "failed")
        db.commit()

    retried = dispatch_terminal_step(
        client,
        resource["id"],
        access_token(admin_headers),
        run["id"],
        step["id"],
        "retry",
    )
    assert retried["status"] == "dispatched"
    assert len(connections) == 2
    assert connections[1].sftp.lstats == connections[0].sftp.lstats
    updated = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert updated["steps"][0]["retry_count"] == 1


def test_terminal_market_write_failure_enters_retry_state(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    resource, run = create_market_start_run(client, admin_headers, monkeypatch)
    step = run["steps"][0]
    connection = FakeConnection()

    def fail_write(_data: str) -> None:
        raise BrokenPipeError("terminal closed")

    connection.process.stdin.write = fail_write

    async def fake_connect(**_):
        return connection

    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)
    response = dispatch_terminal_step(
        client,
        resource["id"],
        access_token(admin_headers),
        run["id"],
        step["id"],
    )

    assert response["code"] == "SSH_COMMAND_DISPATCH_FAILED"
    updated = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert updated["status"] == "awaiting_step_retry"
    assert updated["steps"][0]["status"] == "failed"
    assert updated["steps"][0]["result_summary"]["dispatch_error"] == response["code"]


def test_terminal_workflow_command_dispatches_slnic_stop_and_waits_for_completion(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    resource, run = create_slnic_start_stop_run(client, admin_headers)
    token = access_token(admin_headers)
    connections: typing.List[FakeConnection] = []

    async def fake_connect(**_):
        connection = FakeConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)

    start_step = run["steps"][0]
    with client.websocket_connect(terminal_url(resource["id"], token)) as websocket:
        assert websocket.receive_json()["status"] == "connecting"
        assert websocket.receive_json()["status"] == "connected"
        assert "remote-ready" in websocket.receive_json()["data"]
        websocket.send_json(
            {
                "type": "workflow_step_command",
                "run_id": run["id"],
                "step_id": start_step["id"],
                "operation": "start",
            }
        )
        response = websocket.receive_json()
        assert response["status"] == "dispatched"
        assert response["command"] == terminal_service._build_terminal_command(
            "/tmp/openslt/tcpdump", ["./start_slnic_dump.sh"]
        )

    completed = client.post(f"/api/v1/runs/{run['id']}/steps/{start_step['id']}/complete", headers=admin_headers)
    assert completed.status_code == 200, completed.text
    after_start = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    stop_step = after_start["steps"][1]
    assert after_start["status"] == "awaiting_step_start"
    assert stop_step["status"] == "pending"

    with client.websocket_connect(terminal_url(resource["id"], token)) as websocket:
        assert websocket.receive_json()["status"] == "connecting"
        assert websocket.receive_json()["status"] == "connected"
        assert "remote-ready" in websocket.receive_json()["data"]
        websocket.send_json(
            {
                "type": "workflow_step_command",
                "run_id": run["id"],
                "step_id": stop_step["id"],
                "operation": "start",
            }
        )
        response = websocket.receive_json()
        assert response["type"] == "workflow_command"
        assert response["status"] == "dispatched"
        assert response["command"] == terminal_service._build_terminal_command(
            "/tmp/openslt/tcpdump", ["./stop_slnic_dump.sh"]
        )

    assert "./start_slnic_dump.sh" in connections[0].process.stdin.writes[0]
    assert "./stop_slnic_dump.sh" in connections[1].process.stdin.writes[0]
    updated = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert updated["status"] == "awaiting_step_completion"
    updated_step = updated["steps"][1]
    assert updated_step["status"] == "waiting"
    assert updated_step["progress"] == 100
    assert updated_step["result_summary"]["resource_id"] == resource["id"]
    assert updated_step["result_summary"]["resource_name"] == resource["name"]
    assert updated_step["result_summary"]["mode"] == "terminal"
    assert updated_step["result_summary"]["commands"] == ["./stop_slnic_dump.sh"]
    assert updated_step["result_summary"]["remote_workdir"] == "/tmp/openslt/tcpdump"


def test_terminal_workflow_command_write_failure_enters_retry_state(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    resource, run = create_slnic_start_run(client, admin_headers, ["printf ready"])
    token = access_token(admin_headers)

    async def fake_connect(**_):
        connection = FakeConnection()

        def fail_write(_data: str) -> None:
            raise BrokenPipeError("terminal closed")

        connection.process.stdin.write = fail_write
        return connection

    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)

    step = run["steps"][0]
    with client.websocket_connect(terminal_url(resource["id"], token)) as websocket:
        assert websocket.receive_json()["status"] == "connecting"
        assert websocket.receive_json()["status"] == "connected"
        assert "remote-ready" in websocket.receive_json()["data"]
        websocket.send_json(
            {
                "type": "workflow_step_command",
                "run_id": run["id"],
                "step_id": step["id"],
                "operation": "start",
            }
        )
        response = websocket.receive_json()

    assert response == {
        "type": "workflow_command",
        "status": "failed",
        "code": "SSH_COMMAND_DISPATCH_FAILED",
        "message": "SSH 终端命令下发失败，请重试",
    }
    updated = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert updated["status"] == "awaiting_step_retry"
    assert updated["steps"][0]["status"] == "failed"
    assert updated["steps"][0]["progress"] == 0
    assert updated["steps"][0]["result_summary"]["dispatch_error"] == "SSH_COMMAND_DISPATCH_FAILED"


def test_terminal_workflow_command_dispatches_slnic_merge_and_collects_artifact_on_complete(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    resource, run = create_slnic_start_stop_merge_run(client, admin_headers)
    token = access_token(admin_headers)
    connections: typing.List[FakeConnection] = []

    async def fake_connect(**_):
        connection = FakeConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)
    monkeypatch.setattr(workflows.asyncssh, "connect", fake_connect)

    start_step = run["steps"][0]
    with client.websocket_connect(terminal_url(resource["id"], token)) as websocket:
        websocket.receive_json()
        websocket.receive_json()
        websocket.receive_json()
        websocket.send_json({"type": "workflow_step_command", "run_id": run["id"], "step_id": start_step["id"], "operation": "start"})
        assert "./start_slnic_dump.sh" in websocket.receive_json()["command"]
    completed_start = client.post(f"/api/v1/runs/{run['id']}/steps/{start_step['id']}/complete", headers=admin_headers)
    assert completed_start.status_code == 200, completed_start.text

    after_start = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    stop_step = after_start["steps"][1]
    with client.websocket_connect(terminal_url(resource["id"], token)) as websocket:
        websocket.receive_json()
        websocket.receive_json()
        websocket.receive_json()
        websocket.send_json({"type": "workflow_step_command", "run_id": run["id"], "step_id": stop_step["id"], "operation": "start"})
        assert "./stop_slnic_dump.sh" in websocket.receive_json()["command"]
    completed_stop = client.post(f"/api/v1/runs/{run['id']}/steps/{stop_step['id']}/complete", headers=admin_headers)
    assert completed_stop.status_code == 200, completed_stop.text

    after_stop = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    merge_step = after_stop["steps"][2]
    with client.websocket_connect(terminal_url(resource["id"], token)) as websocket:
        assert websocket.receive_json()["status"] == "connecting"
        assert websocket.receive_json()["status"] == "connected"
        assert "remote-ready" in websocket.receive_json()["data"]
        websocket.send_json(
            {
                "type": "workflow_step_command",
                "run_id": run["id"],
                "step_id": merge_step["id"],
                "operation": "start",
            }
        )
        response = websocket.receive_json()
        assert response["type"] == "workflow_command"
        assert response["status"] == "dispatched"
        assert "./pcap_merge_tool slnic*" in response["command"]
        assert "./editcap merge_pcap.pcap merge_pcap.pcapng" in response["command"]

    assert "./pcap_merge_tool slnic*" in connections[2].process.stdin.writes[0]
    before_complete = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert before_complete["status"] == "awaiting_step_completion"
    assert before_complete["steps"][2]["result_summary"]["mode"] == "terminal"
    assert "artifact_id" not in before_complete["steps"][2]["result_summary"]

    completed_merge = client.post(f"/api/v1/runs/{run['id']}/steps/{merge_step['id']}/complete", headers=admin_headers)
    assert completed_merge.status_code == 200, completed_merge.text
    completed = completed_merge.json()
    assert completed["status"] == "completed"
    assert completed["steps"][2]["status"] == "succeeded"
    assert completed["steps"][2]["result_summary"]["filename"] == "merge_pcap.pcapng"
    assert completed["steps"][2]["result_summary"]["size"] == len(b"merged-pcapng")
    assert completed["artifacts"][0]["name"] == "merge_pcap.pcapng"
    assert connections[3].sftp.gets[0][0] == "/tmp/openslt/tcpdump/merge_pcap.pcapng"


def test_terminal_slnic_merge_cannot_complete_without_fixed_artifact(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    resource, run = create_slnic_merge_run(client, admin_headers, ["printf custom-merge"])
    token = access_token(admin_headers)
    connections: typing.List[FakeConnection] = []

    async def fake_connect(**_):
        connection = FakeConnection()
        if connections:
            async def missing_get(_remote_path: str, _local_path: str) -> None:
                raise FileNotFoundError("merge_pcap.pcapng")

            connection.sftp.get = missing_get
        connections.append(connection)
        return connection

    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)
    monkeypatch.setattr(workflows.asyncssh, "connect", fake_connect)
    step = run["steps"][0]

    with client.websocket_connect(terminal_url(resource["id"], token)) as websocket:
        websocket.receive_json()
        websocket.receive_json()
        websocket.receive_json()
        websocket.send_json({
            "type": "workflow_step_command",
            "run_id": run["id"],
            "step_id": step["id"],
            "operation": "start",
        })
        dispatched = websocket.receive_json()
    assert dispatched["commands"] == ["printf custom-merge"]

    completed = client.post(
        f"/api/v1/runs/{run['id']}/steps/{step['id']}/complete",
        headers=admin_headers,
    )
    assert completed.status_code == 409, completed.text
    assert completed.json()["code"] == "SLNIC_ARTIFACT_COLLECT_FAILED"
    current = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert current["status"] == "awaiting_step_completion"
    assert current["steps"][0]["status"] == "waiting"
    assert current["artifacts"] == []


def test_terminal_workflow_command_rejects_wrong_resource(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    _resource, run = create_slnic_start_run(client, admin_headers)
    other_resource = create_resource(client, admin_headers, "SLNIC-Other", resource_type="slnic")
    token = access_token(admin_headers)
    connection = FakeConnection()

    async def fake_connect(**_):
        return connection

    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)

    with client.websocket_connect(terminal_url(other_resource["id"], token)) as websocket:
        assert websocket.receive_json()["status"] == "connecting"
        assert websocket.receive_json()["status"] == "connected"
        assert "remote-ready" in websocket.receive_json()["data"]
        websocket.send_json(
            {
                "type": "workflow_step_command",
                "run_id": run["id"],
                "step_id": run["steps"][0]["id"],
                "operation": "start",
            }
        )
        response = websocket.receive_json()
        assert response["type"] == "workflow_command"
        assert response["status"] == "failed"
        assert response["code"] == "INVALID_RESOURCE"

    assert connection.process.stdin.writes == []
    unchanged = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert unchanged["status"] == "awaiting_step_start"
    assert unchanged["steps"][0]["status"] == "pending"


def test_generic_terminal_rejects_order_workflow_start(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    resource, run = create_order_start_run(client, admin_headers, monkeypatch)
    step = run["steps"][0]
    token = access_token(admin_headers)
    connection = FakeConnection()

    async def fake_connect(**_):
        return connection

    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)

    with client.websocket_connect(terminal_url(resource["id"], token)) as websocket:
        assert websocket.receive_json()["status"] == "connecting"
        assert websocket.receive_json()["status"] == "connected"
        assert "remote-ready" in websocket.receive_json()["data"]
        websocket.send_json(
            {
                "type": "workflow_step_command",
                "run_id": run["id"],
                "step_id": step["id"],
                "operation": "start",
            }
        )
        response = websocket.receive_json()
        assert response["type"] == "workflow_command"
        assert response["status"] == "failed"
        assert response["code"] == "INVALID_WORKFLOW_STEP"

    assert connection.process.stdin.writes == []
    updated = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert updated["status"] == "awaiting_step_start"
    assert updated["steps"][0]["status"] == "pending"


def test_order_actions_can_repeat_and_completion_cleans_session(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    _resource, run = create_order_start_run(client, admin_headers, monkeypatch)
    # Put the prepared node into the same waiting state produced by the tmux launcher.
    db = SessionLocal()
    try:
        stored = db.get(RunModel, run["id"])
        step = stored.steps[0]
        transition_run(stored, "awaiting_step_completion")
        transition_step(step, "waiting")
        step.result_summary = {
            "process_started": True,
            "tmux_session": "openslt-order-r1-s1",
            "order_action_status": "pending",
        }
        db.commit()
    finally:
        db.close()
    sent = []

    async def fake_send(_resource, session, action):
        sent.append((session, action))

    async def fake_cleanup(_resource, _session):
        return True

    monkeypatch.setattr(runs_route, "send_order_action", fake_send)
    monkeypatch.setattr(runs_route, "cleanup_order_session", fake_cleanup)
    step_id = run["steps"][0]["id"]
    response = client.post(f"/api/v1/runs/{run['id']}/steps/{step_id}/order-action", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert sent == [("openslt-order-r1-s1", "new_order")]
    quote = client.post(
        f"/api/v1/runs/{run['id']}/steps/{step_id}/order-action",
        headers=admin_headers,
        json={"action": "new_quote"},
    )
    repeated = client.post(
        f"/api/v1/runs/{run['id']}/steps/{step_id}/order-action",
        headers=admin_headers,
        json={"action": "new_quote"},
    )
    assert quote.status_code == 200, quote.text
    assert repeated.status_code == 200, repeated.text
    assert sent == [
        ("openslt-order-r1-s1", "new_order"),
        ("openslt-order-r1-s1", "new_quote"),
        ("openslt-order-r1-s1", "new_quote"),
    ]
    history = repeated.json()["steps"][0]["result_summary"]["order_action_history"]
    assert [item["action"] for item in history] == ["new_order", "new_quote", "new_quote"]
    assert all(item["status"] == "dispatched" for item in history)
    completed = client.post(f"/api/v1/runs/{run['id']}/steps/{step_id}/complete", headers=admin_headers)
    assert completed.status_code == 200, completed.text


def test_order_step_can_complete_without_sending_action(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    _resource, run = create_order_start_run(client, admin_headers, monkeypatch)
    db = SessionLocal()
    try:
        stored = db.get(RunModel, run["id"])
        step = stored.steps[0]
        transition_run(stored, "awaiting_step_completion")
        transition_step(step, "waiting")
        step.result_summary = {
            "process_started": True,
            "tmux_session": "openslt-order-r1-s1",
            "order_action_status": "pending",
            "order_action_history": [],
        }
        db.commit()
    finally:
        db.close()
    cleaned = []

    async def fake_cleanup(_resource, session):
        cleaned.append(session)
        return True

    monkeypatch.setattr(runs_route, "cleanup_order_session", fake_cleanup)
    step_id = run["steps"][0]["id"]
    response = client.post(f"/api/v1/runs/{run['id']}/steps/{step_id}/complete", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert cleaned == ["openslt-order-r1-s1"]


def test_order_action_rejects_action_not_supported_by_resource(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    resource, run = create_order_start_run(client, admin_headers, monkeypatch)
    db = SessionLocal()
    try:
        stored_resource = db.get(Resource, resource["id"])
        stored_resource.capabilities = {**stored_resource.capabilities, "order_actions": ["new_order"]}
        stored = db.get(RunModel, run["id"])
        transition_run(stored, "awaiting_step_completion")
        transition_step(stored.steps[0], "waiting")
        stored.steps[0].result_summary = {"order_action_status": "pending"}
        db.commit()
    finally:
        db.close()

    response = client.post(
        f"/api/v1/runs/{run['id']}/steps/{run['steps'][0]['id']}/order-action",
        headers=admin_headers,
        json={"action": "new_quote"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "ORDER_ACTION_UNSUPPORTED"


def test_unknown_order_action_is_recorded_and_can_be_confirmed(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    _resource, run = create_order_start_run(client, admin_headers, monkeypatch)
    db = SessionLocal()
    try:
        stored = db.get(RunModel, run["id"])
        transition_run(stored, "awaiting_step_completion")
        transition_step(stored.steps[0], "waiting")
        stored.steps[0].result_summary = {"order_action_status": "pending"}
        db.commit()
    finally:
        db.close()

    async def fail_send(_resource, _session, _action):
        raise WorkflowError("ORDER_SESSION_LOST", "session lost", 409)

    monkeypatch.setattr(runs_route, "send_order_action", fail_send)
    step_id = run["steps"][0]["id"]
    failed = client.post(
        f"/api/v1/runs/{run['id']}/steps/{step_id}/order-action",
        headers=admin_headers,
        json={"action": "new_order_simple"},
    )
    assert failed.status_code == 409
    current = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    summary = current["steps"][0]["result_summary"]
    assert summary["order_action_status"] == "unknown"
    assert summary["order_action_history"][-1]["status"] == "unknown"
    blocked = client.post(f"/api/v1/runs/{run['id']}/steps/{step_id}/complete", headers=admin_headers)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "ORDER_ACTION_UNRESOLVED"

    confirmed = client.post(
        f"/api/v1/runs/{run['id']}/steps/{step_id}/order-action/confirm",
        headers=admin_headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    confirmed_history = confirmed.json()["steps"][0]["result_summary"]["order_action_history"]
    assert confirmed_history[-1]["status"] == "dispatched"
    assert confirmed_history[-1]["confirmed_by"] == 1


def test_order_action_history_is_limited_to_one_hundred_entries(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    _resource, run = create_order_start_run(client, admin_headers, monkeypatch)
    db = SessionLocal()
    try:
        stored = db.get(RunModel, run["id"])
        transition_run(stored, "awaiting_step_completion")
        transition_step(stored.steps[0], "waiting")
        stored.steps[0].result_summary = {
            "order_action_status": "dispatched",
            "order_action_history": [
                {"request_id": "old-%s" % index, "action": "new_order", "status": "dispatched"}
                for index in range(100)
            ],
        }
        db.commit()
    finally:
        db.close()

    async def fake_send(_resource, _session, _action):
        return None

    monkeypatch.setattr(runs_route, "send_order_action", fake_send)
    step_id = run["steps"][0]["id"]
    response = client.post(f"/api/v1/runs/{run['id']}/steps/{step_id}/order-action", headers=admin_headers)
    assert response.status_code == 200, response.text
    history = response.json()["steps"][0]["result_summary"]["order_action_history"]
    assert len(history) == 100
    assert history[0]["request_id"] == "old-1"
    assert history[-1]["request_id"] != "old-99"


def test_unknown_order_action_can_restart_the_whole_node(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    _resource, run = create_order_start_run(client, admin_headers, monkeypatch)
    db = SessionLocal()
    try:
        stored = db.get(RunModel, run["id"])
        step = stored.steps[0]
        transition_run(stored, "awaiting_step_completion")
        transition_step(step, "waiting")
        step.result_summary = {
            "process_started": True,
            "tmux_session": "openslt-order-r1-s1",
            "order_action_status": "unknown",
            "order_action_history": [{
                "request_id": "unknown-1",
                "action": "new_order",
                "status": "unknown",
            }],
        }
        db.commit()
    finally:
        db.close()
    cleaned = []

    async def fake_cleanup(_resource, session):
        cleaned.append(session)
        return True

    monkeypatch.setattr(runs_route, "cleanup_order_session", fake_cleanup)
    monkeypatch.setattr(runs_route, "schedule_task", lambda _task_id: None)
    step_id = run["steps"][0]["id"]
    response = client.post(f"/api/v1/runs/{run['id']}/steps/{step_id}/retry", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert cleaned == ["openslt-order-r1-s1"]
    assert response.json()["status"] == "running"
    assert response.json()["steps"][0]["retry_count"] == 1
    assert response.json()["steps"][0]["result_summary"]["order_action_history"][0]["request_id"] == "unknown-1"


def test_remote_terminal_reports_connection_failure(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    resource = create_resource(client, admin_headers, "REM-Unreachable")
    token = access_token(admin_headers)

    async def failed_connect(**_):
        raise OSError("connection refused")

    monkeypatch.setattr(terminal_service.asyncssh, "connect", failed_connect)

    with client.websocket_connect(terminal_url(resource["id"], token)) as websocket:
        assert websocket.receive_json()["status"] == "connecting"
        error = websocket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "SSH_CONNECTION_FAILED"
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_json()
        assert closed.value.code == 4511

    db = SessionLocal()
    try:
        audits = list(
            db.query(AuditLog).filter(
                AuditLog.object_id == str(resource["id"]),
                AuditLog.action == "resource.terminal.open",
            )
        )
        assert len(audits) == 1
        assert audits[0].result == "failed"
        assert audits[0].detail == {"error_type": "OSError"}
    finally:
        db.close()


def test_terminal_reports_invalid_stored_credentials(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
):
    resource = create_resource(client, admin_headers, "REM-Bad-Credential")
    token = access_token(admin_headers)
    other_key = Fernet.generate_key()
    db = SessionLocal()
    try:
        row = db.get(Resource, resource["id"])
        row.encrypted_password = Fernet(other_key).encrypt(b"secret").decode()
        db.commit()
    finally:
        db.close()

    with client.websocket_connect(terminal_url(resource["id"], token)) as websocket:
        error = websocket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "RESOURCE_CREDENTIAL_INVALID"
        assert "无法解密" in error["message"]
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_json()
        assert closed.value.code == 4512

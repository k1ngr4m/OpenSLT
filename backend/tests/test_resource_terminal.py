from __future__ import annotations

import typing
import asyncio

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.database import SessionLocal
from app.models import AuditLog, Resource
from app.services import order_configs
from app.services import terminal as terminal_service
from app.services import workflows
from conftest import create_plan_scenario, create_resource, publish_workflow


def access_token(headers: typing.Dict[str, str]) -> str:
    return headers["Authorization"][len("Bearer ") :]


def terminal_url(resource_id: int, token: str) -> str:
    return f"/api/v1/ws/resources/{resource_id}/terminal?token={token}"


def slnic_start_nodes() -> list[dict]:
    return [
        {
            "node_key": "slnic-start",
            "node_type": "slnic_start_capture",
            "name": "启动 SLNIC 节点",
            "config": {},
        }
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


def create_slnic_start_run(client: TestClient, headers: typing.Dict[str, str]) -> tuple[dict, dict]:
    resource = create_resource(client, headers, "SLNIC-Terminal", resource_type="slnic")
    plan, scenario = create_plan_scenario(client, headers, required_types=["slnic"], resource_ids=[resource["id"]])
    publish_workflow(client, headers, scenario, [resource["id"]], slnic_start_nodes())
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

    monkeypatch.setattr(workflows.order_config_service, "read", fake_read_order_config)
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


class FakeConnection:
    def __init__(self) -> None:
        self.process = FakeProcess()
        self.command: typing.Union[str, None] = None
        self.process_options: dict = {}
        self.closed = False

    async def create_process(self, command: typing.Union[str, None], **options):
        self.command = command
        self.process_options = options
        return self.process

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


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
        assert response["command"] == "cd /tmp/openslt/tcpdump && ./start_slnic_dump.sh"

    assert connection.process.stdin.writes == ["cd /tmp/openslt/tcpdump && ./start_slnic_dump.sh\r"]
    updated = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert updated["status"] == "awaiting_step_completion"
    updated_step = updated["steps"][0]
    assert updated_step["status"] == "waiting"
    assert updated_step["progress"] == 100
    assert updated_step["result_summary"]["resource_id"] == resource["id"]
    assert updated_step["result_summary"]["resource_name"] == resource["name"]
    assert updated_step["result_summary"]["mode"] == "terminal"
    assert updated_step["result_summary"]["exit_code"] is None


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


def test_terminal_workflow_command_dispatches_order_command_after_preparation(
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

    async def fake_prepare_order_node(*_):
        return {
            "prepared": True,
            "xml_filename": "order.xml",
            "xml_checksum": "abc123",
            "read_symbol_csv": 0,
            "network_interface": "p4p1",
            "contract_files": [],
            "generated_command": "cd /tmp/openslt && export ZF_ATTR=interface=p4p1 && ./openslt order.xml",
            "process_started": False,
        }

    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)
    monkeypatch.setattr(workflows, "prepare_order_node", fake_prepare_order_node)

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
        assert response["command"] == "cd /tmp/openslt && export ZF_ATTR=interface=p4p1 && ./openslt order.xml"

    assert connection.process.stdin.writes == ["cd /tmp/openslt && export ZF_ATTR=interface=p4p1 && ./openslt order.xml\r"]
    updated = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert updated["status"] == "awaiting_step_completion"
    updated_step = updated["steps"][0]
    assert updated_step["status"] == "waiting"
    assert updated_step["result_summary"]["mode"] == "terminal"
    assert updated_step["result_summary"]["resource_id"] == resource["id"]
    assert updated_step["result_summary"]["resource_name"] == resource["name"]
    assert updated_step["result_summary"]["command"] == "cd /tmp/openslt && export ZF_ATTR=interface=p4p1 && ./openslt order.xml"
    assert updated_step["result_summary"]["process_started"] is True


def test_terminal_workflow_command_marks_order_preparation_failure_retryable(
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

    async def failed_prepare_order_node(*_):
        raise workflows.WorkflowError("ORDER_CONFIG_CHANGED", "XML 配置校验值与发布版本不一致", 409)

    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)
    monkeypatch.setattr(workflows, "prepare_order_node", failed_prepare_order_node)

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
        assert response["code"] == "ORDER_CONFIG_CHANGED"

    assert connection.process.stdin.writes == []
    updated = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert updated["status"] == "awaiting_step_retry"
    assert updated["steps"][0]["status"] == "failed"
    assert updated["steps"][0]["error_message"] == "XML 配置校验值与发布版本不一致"


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

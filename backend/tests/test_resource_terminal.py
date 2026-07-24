from __future__ import annotations

import typing
import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import AuditLog, Resource
from app.services import terminal as terminal_service
from conftest import create_resource


def access_token(headers: typing.Dict[str, str]) -> str:
    return headers["Authorization"][len("Bearer ") :]


def terminal_url(resource_id: int, token: str) -> str:
    return f"/api/v1/ws/resources/{resource_id}/terminal?token={token}"


def test_simulated_terminal_commands_and_audit(client: TestClient, admin_headers: typing.Dict[str, str]):
    resource = create_resource(client, admin_headers, "REM-Terminal")
    token = access_token(admin_headers)

    with client.websocket_connect(terminal_url(resource["id"], token)) as websocket:
        connecting = websocket.receive_json()
        connected = websocket.receive_json()
        banner = websocket.receive_json()
        assert connecting == {
            "type": "status",
            "status": "connecting",
            "mode": "simulated",
            "message": "正在建立终端会话",
        }
        assert connected["status"] == "connected"
        assert connected["mode"] == "simulated"
        assert "安全模拟终端" in banner["data"]

        websocket.send_json({"type": "input", "data": "pwd\r"})
        assert "/tmp/openslt" in websocket.receive_json()["data"]
        websocket.send_json({"type": "input", "data": "secret-command --password hidden\r"})
        assert "command not found (simulated)" in websocket.receive_json()["data"]
        websocket.send_json({"type": "input", "data": "cd /var/log\r\npwd\r\n"})
        assert "/var/log" in websocket.receive_json()["data"]
        websocket.send_json({"type": "resize", "cols": 160, "rows": 48})
        websocket.send_json({"type": "input", "data": "exit\r"})
        assert "logout" in websocket.receive_json()["data"]
        assert websocket.receive_json() == {"type": "exit", "exit_code": 0}
        assert websocket.receive_json()["status"] == "closed"

    db = SessionLocal()
    try:
        audits = list(
            db.query(AuditLog)
            .filter(AuditLog.object_id == str(resource["id"]), AuditLog.action.like("resource.terminal.%"))
            .order_by(AuditLog.id)
        )
        assert [item.action for item in audits] == ["resource.terminal.open", "resource.terminal.close"]
        assert audits[0].detail == {"mode": "simulated"}
        assert audits[1].detail["reason"] == "shell_exit"
        assert "secret-command" not in str([item.detail for item in audits])
        assert "hidden" not in str([item.detail for item in audits])
    finally:
        db.close()


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

    monkeypatch.setattr(settings, "execution_mode", "remote")
    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)

    with client.websocket_connect(terminal_url(resource["id"], token)) as websocket:
        assert websocket.receive_json()["status"] == "connecting"
        connected = websocket.receive_json()
        assert connected["status"] == "connected"
        assert connected["mode"] == "remote"
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
    assert connection.process.closed
    assert connection.closed


def test_remote_terminal_reports_connection_failure(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    resource = create_resource(client, admin_headers, "REM-Unreachable")
    token = access_token(admin_headers)

    async def failed_connect(**_):
        raise OSError("connection refused")

    monkeypatch.setattr(settings, "execution_mode", "remote")
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
        assert audits[0].detail == {"mode": "remote", "error_type": "OSError"}
    finally:
        db.close()

from __future__ import annotations

import shlex
import types

import pytest

from app.services import order_sessions


class FakeResult:
    def __init__(self, exit_status: int = 0, stdout: str = "", stderr: str = ""):
        self.exit_status = exit_status
        self.stdout = stdout
        self.stderr = stderr


class FakeConnection:
    def __init__(self):
        self.commands = []
        self.sessions = set()
        self.dead_sessions = set()
        self.pane_output = ""
        self.pane_exit_status = 0
        self.exit_after_respawn = False
        self.command_failures = {}
        self.closed = False

    async def run(self, command, check=False):
        self.commands.append(command)
        for prefix, result in self.command_failures.items():
            if command.startswith(prefix):
                return result
        if command.startswith("command -v tmux"):
            return FakeResult(0)
        if command.startswith("test -d") or command.startswith("test -x"):
            return FakeResult(0)
        if command.startswith("tmux has-session"):
            session = command.split("-t ", 1)[1].split(" ", 1)[0].strip("'")
            return FakeResult(0 if session in self.sessions else 1)
        if command.startswith("tmux new-session"):
            session = command.split("-s ", 1)[1].split(" ", 1)[0].strip("'")
            self.sessions.add(session)
            return FakeResult(0)
        if command.startswith("tmux respawn-pane"):
            session = command.split("-t ", 1)[1].split(" ", 1)[0].strip("'")
            if self.exit_after_respawn:
                self.dead_sessions.add(session)
            return FakeResult(0)
        if command.startswith("tmux display-message"):
            session = command.split("-t ", 1)[1].split(" ", 1)[0].split(":", 1)[0].strip("'")
            if session not in self.sessions:
                return FakeResult(1)
            if session in self.dead_sessions:
                return FakeResult(0, "1|%s\n" % self.pane_exit_status)
            return FakeResult(0, "0|\n")
        if command.startswith("tmux capture-pane"):
            return FakeResult(0, self.pane_output)
        if command.startswith("tmux kill-session"):
            session = command.split("-t ", 1)[1].split(" ", 1)[0].strip("'")
            self.sessions.discard(session)
            self.dead_sessions.discard(session)
            return FakeResult(0)
        return FakeResult(0)

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


@pytest.mark.asyncio
async def test_launch_order_session_uses_safe_tmux_command(monkeypatch):
    connection = FakeConnection()

    async def connect(**_):
        return connection

    monkeypatch.setattr(order_sessions.asyncssh, "connect", connect)
    monkeypatch.setattr(order_sessions, "ssh_options", lambda _resource: {})
    monkeypatch.setattr(order_sessions.asyncio, "sleep", lambda _seconds: _completed())
    resource = types.SimpleNamespace(
        remote_path="/tmp/tool",
        capabilities={"order_tool": "binary", "order_actions": ["new_order"]},
    )
    run = types.SimpleNamespace(id=12)
    step = types.SimpleNamespace(id=34)
    result = await order_sessions.launch_order_session(
        resource,
        run,
        step,
        "cd /tmp/tool && ./binary order.xml",
    )
    assert result["tmux_session"] == "openslt-order-r12-s34"
    assert result["supported_order_actions"] == ["new_order"]
    assert result["order_action_history"] == []
    launch = next(command for command in connection.commands if command.startswith("tmux respawn-pane"))
    assert shlex.split(launch) == [
        "tmux",
        "respawn-pane",
        "-k",
        "-t",
        "openslt-order-r12-s34",
        "/bin/sh -lc 'cd /tmp/tool && ./binary order.xml'",
    ]
    assert any("remain-on-exit on" in command for command in connection.commands)
    assert "test -d /tmp/tool" in connection.commands
    assert "test -x /tmp/tool/binary" in connection.commands
    assert all("; rm" not in command for command in connection.commands)


async def _completed():
    return None


@pytest.mark.asyncio
async def test_launch_order_session_reports_immediate_process_exit(monkeypatch):
    connection = FakeConnection()
    connection.exit_after_respawn = True
    connection.pane_exit_status = 127
    connection.pane_output = "/bin/sh: ./binary: not found\n"

    async def connect(**_):
        return connection

    monkeypatch.setattr(order_sessions.asyncssh, "connect", connect)
    monkeypatch.setattr(order_sessions, "ssh_options", lambda _resource: {})
    monkeypatch.setattr(order_sessions.asyncio, "sleep", lambda _seconds: _completed())

    with pytest.raises(Exception) as exc_info:
        await order_sessions.launch_order_session(
            types.SimpleNamespace(remote_path="/tmp/tool", capabilities={"order_tool": "binary"}),
            types.SimpleNamespace(id=12),
            types.SimpleNamespace(id=34),
            "cd /tmp/tool && ./binary order.xml",
        )

    assert getattr(exc_info.value, "code", "") == "ORDER_SESSION_START_FAILED"
    assert "退出码 127" in str(exc_info.value)
    assert "./binary: not found" in str(exc_info.value)
    assert "openslt-order-r12-s34" in connection.sessions


@pytest.mark.asyncio
async def test_launch_order_session_rejects_missing_binary(monkeypatch):
    connection = FakeConnection()
    connection.command_failures["test -x"] = FakeResult(1)

    async def connect(**_):
        return connection

    monkeypatch.setattr(order_sessions.asyncssh, "connect", connect)
    monkeypatch.setattr(order_sessions, "ssh_options", lambda _resource: {})

    with pytest.raises(Exception) as exc_info:
        await order_sessions.launch_order_session(
            types.SimpleNamespace(remote_path="/tmp/tool", capabilities={"order_tool": "binary"}),
            types.SimpleNamespace(id=12),
            types.SimpleNamespace(id=34),
            "cd /tmp/tool && ./binary order.xml",
        )

    assert getattr(exc_info.value, "code", "") == "ORDER_BINARY_REQUIRED"
    assert "/tmp/tool/binary" in str(exc_info.value)
    assert not any(command.startswith("tmux new-session") for command in connection.commands)


@pytest.mark.asyncio
async def test_send_order_action_is_literal_and_rejects_unsupported(monkeypatch):
    connection = FakeConnection()
    connection.sessions.add("openslt-order-r1-s2")

    async def connect(**_):
        return connection

    monkeypatch.setattr(order_sessions.asyncssh, "connect", connect)
    monkeypatch.setattr(order_sessions, "ssh_options", lambda _resource: {})
    resource = types.SimpleNamespace(capabilities={"order_actions": ["new_order"]})
    await order_sessions.send_order_action(resource, "openslt-order-r1-s2", "new_order")
    assert any("-l new_order" in command for command in connection.commands)
    with pytest.raises(Exception) as exc_info:
        await order_sessions.send_order_action(resource, "openslt-order-r1-s2", "new_quote")
    assert getattr(exc_info.value, "code", "") == "ORDER_ACTION_UNSUPPORTED"


@pytest.mark.asyncio
async def test_send_order_action_supports_cxl_quote(monkeypatch):
    connection = FakeConnection()
    connection.sessions.add("openslt-order-r1-s2")

    async def connect(**_):
        return connection

    monkeypatch.setattr(order_sessions.asyncssh, "connect", connect)
    monkeypatch.setattr(order_sessions, "ssh_options", lambda _resource: {})
    resource = types.SimpleNamespace(capabilities={"order_actions": ["cxl_quote"]})

    await order_sessions.send_order_action(resource, "openslt-order-r1-s2", "cxl_quote")

    assert any("-l cxl_quote" in command for command in connection.commands)

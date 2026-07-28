from __future__ import annotations

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
        self.closed = False

    async def run(self, command, check=False):
        self.commands.append(command)
        if command.startswith("command -v tmux"):
            return FakeResult(0)
        if command.startswith("tmux has-session"):
            session = command.split("-t ", 1)[1].split(" ", 1)[0].strip("'")
            return FakeResult(0 if session in self.sessions else 1)
        if command.startswith("tmux new-session"):
            session = command.split("-s ", 1)[1].split(" ", 1)[0].strip("'")
            self.sessions.add(session)
            return FakeResult(0)
        if command.startswith("tmux kill-session"):
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
    resource = types.SimpleNamespace(capabilities={"order_actions": ["new_order"]})
    run = types.SimpleNamespace(id=12)
    step = types.SimpleNamespace(id=34)
    result = await order_sessions.launch_order_session(
        resource,
        run,
        step,
        "cd /tmp/tool && ./binary order.xml",
    )
    assert result["tmux_session"] == "openslt-order-r12-s34"
    assert any("tmux new-session" in command for command in connection.commands)
    assert all("; rm" not in command for command in connection.commands)


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

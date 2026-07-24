from __future__ import annotations

import typing
import asyncio
import posixpath
import shlex
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

import asyncssh
import jwt
from fastapi import WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import trace_id_ctx
from app.core.security import decode_token, decrypt_secret
from app.models import Resource, User
from app.services.audit import write_audit


TERMINAL_RESOURCE_TYPES = {"rem", "market", "order", "slnic"}
MAX_INPUT_SIZE = 64 * 1024
MIN_COLUMNS = 20
MAX_COLUMNS = 300
MIN_ROWS = 5
MAX_ROWS = 120


@dataclass
class TerminalResource:
    id: int
    name: str
    resource_type: str
    host: str
    port: int
    username: str
    password: typing.Union[str, None]
    private_key: typing.Union[str, None]
    remote_path: str


def _clamp(value: object, minimum: int, maximum: int, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, parsed))


async def _send(websocket: WebSocket, payload: dict) -> bool:
    try:
        await websocket.send_json(payload)
        return True
    except (RuntimeError, WebSocketDisconnect):
        return False


async def _close(websocket: WebSocket, code: int = 1000) -> None:
    with suppress(RuntimeError, WebSocketDisconnect):
        await websocket.close(code=code)


def _audit(
    websocket: WebSocket,
    actor_id: int,
    resource_id: int,
    action: str,
    result: str = "success",
    detail: typing.Union[dict, None] = None,
) -> None:
    db = SessionLocal()
    try:
        actor = db.get(User, actor_id)
        write_audit(
            db,
            action,
            "resource",
            resource_id,
            actor,
            websocket,  # WebSocket exposes the same client and headers used by audit metadata.
            result=result,
            detail=detail,
        )
        db.commit()
    finally:
        db.close()


def _load_context(token: str, resource_id: int) -> typing.Union[typing.Tuple[int, TerminalResource], typing.Tuple[None, str]]:
    try:
        payload = decode_token(token, "access")
        actor_id = int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError, KeyError):
        return None, "登录凭据无效或已过期"

    db = SessionLocal()
    try:
        actor = db.get(User, actor_id)
        if not actor or not actor.is_active:
            return None, "登录凭据无效或已过期"
        if actor.role not in {"admin", "tester"}:
            return None, "当前用户无权使用资源操作台"

        resource = db.get(Resource, resource_id)
        if not resource or resource.is_deleted:
            return None, "资源不存在"
        if resource.resource_type not in TERMINAL_RESOURCE_TYPES:
            return None, "该资源类型不支持 SSH 操作台"
        if not resource.is_enabled:
            return None, "资源已停用，无法打开操作台"

        return actor.id, TerminalResource(
            id=resource.id,
            name=resource.name,
            resource_type=resource.resource_type,
            host=resource.host,
            port=resource.ssh_port,
            username=resource.username,
            password=decrypt_secret(resource.encrypted_password),
            private_key=decrypt_secret(resource.encrypted_private_key),
            remote_path=resource.remote_path,
        )
    finally:
        db.close()


class SimulatedTerminal:
    def __init__(self, resource: TerminalResource) -> None:
        self.resource = resource
        self.home = f"/home/{resource.username or 'user'}"
        self.cwd = posixpath.normpath(resource.remote_path or self.home)
        self.previous_cwd = self.home
        self.buffer = ""
        self.escape_buffer = ""
        self.last_was_carriage_return = False

    def _display_path(self) -> str:
        if self.cwd == self.home:
            return "~"
        if self.cwd.startswith(f"{self.home}/"):
            return f"~{self.cwd[len(self.home):]}"
        return self.cwd

    def prompt(self) -> str:
        user = self.resource.username or "user"
        host = self.resource.host or "simulated-host"
        return f"\x1b[36m[模拟]\x1b[0m \x1b[32m{user}@{host}\x1b[0m:\x1b[34m{self._display_path()}\x1b[0m$ "

    def banner(self) -> str:
        return (
            "\x1b[33mOpenSLT 安全模拟终端\x1b[0m\r\n"
            "当前会话不会连接远程服务器，也不会执行本机命令。输入 help 查看可用命令。\r\n\r\n"
            f"{self.prompt()}"
        )

    def _resolve_path(self, value: str) -> str:
        if value in {"", "~"}:
            return self.home
        if value == "-":
            return self.previous_cwd
        if value.startswith("~/"):
            value = f"{self.home}/{value[2:]}"
        elif not value.startswith("/"):
            value = f"{self.cwd}/{value}"
        return posixpath.normpath(value)

    def _run_command(self, line: str) -> typing.Tuple[str, bool]:
        try:
            parts = shlex.split(line)
        except ValueError as exc:
            return f"模拟终端: {exc}\r\n", False
        if not parts:
            return "", False

        command, *args = parts
        if command == "help":
            return (
                "可用命令: pwd, ls, cd, echo, whoami, hostname, date, uname, clear, help, exit\r\n",
                False,
            )
        if command == "pwd":
            return f"{self.cwd}\r\n", False
        if command == "ls":
            return "bin  conf  logs  scripts\r\n", False
        if command == "cd":
            target = self._resolve_path(args[0] if args else "")
            old_cwd = self.cwd
            self.cwd = target
            self.previous_cwd = old_cwd
            return (f"{self.cwd}\r\n" if args and args[0] == "-" else ""), False
        if command == "echo":
            return f"{' '.join(args)}\r\n", False
        if command == "whoami":
            return f"{self.resource.username or 'user'}\r\n", False
        if command == "hostname":
            return f"{self.resource.host or 'simulated-host'}\r\n", False
        if command == "date":
            return f"{datetime.now(timezone.utc):%a %b %d %H:%M:%S UTC %Y}\r\n", False
        if command == "uname":
            if args == ["-a"]:
                host = self.resource.host or "simulated-host"
                return (
                    f"Linux {host} 5.15.0-openslt #1 SMP x86_64 GNU/Linux (simulated)\r\n",
                    False,
                )
            return "Linux\r\n", False
        if command == "clear":
            return "\x1b[2J\x1b[H", False
        if command == "exit":
            return "logout\r\n", True
        return f"{command}: command not found (simulated)\r\n", False

    def feed(self, data: str) -> typing.Tuple[str, bool]:
        output: typing.List[str] = []
        should_exit = False
        for character in data:
            if self.escape_buffer:
                self.escape_buffer += character
                if character.isalpha() or character == "~" or len(self.escape_buffer) >= 8:
                    self.escape_buffer = ""
                continue
            if character == "\x1b":
                self.escape_buffer = character
                continue
            if character == "\n" and self.last_was_carriage_return:
                self.last_was_carriage_return = False
                continue
            if character in {"\r", "\n"}:
                self.last_was_carriage_return = character == "\r"
                output.append("\r\n")
                command_output, should_exit = self._run_command(self.buffer.strip())
                self.buffer = ""
                output.append(command_output)
                if not should_exit:
                    output.append(self.prompt())
                if should_exit:
                    break
            elif character in {"\x7f", "\b"}:
                self.last_was_carriage_return = False
                if self.buffer:
                    self.buffer = self.buffer[:-1]
                    output.append("\b \b")
            elif character == "\x03":
                self.last_was_carriage_return = False
                self.buffer = ""
                output.append(f"^C\r\n{self.prompt()}")
            elif character == "\x0c":
                self.last_was_carriage_return = False
                output.append(f"\x1b[2J\x1b[H{self.prompt()}{self.buffer}")
            elif character.isprintable() or character == "\t":
                self.last_was_carriage_return = False
                self.buffer += character
                output.append(character)
        return "".join(output), should_exit


async def _receive_simulated(websocket: WebSocket, terminal: SimulatedTerminal) -> str:
    await _send(websocket, {"type": "output", "data": terminal.banner()})
    while True:
        try:
            message = await websocket.receive_json()
        except (WebSocketDisconnect, RuntimeError):
            return "client_disconnected"
        if message.get("type") == "resize":
            continue
        if message.get("type") != "input":
            await _send(websocket, {"type": "error", "code": "INVALID_MESSAGE", "message": "不支持的终端消息"})
            continue
        data = message.get("data")
        if not isinstance(data, str) or len(data.encode("utf-8")) > MAX_INPUT_SIZE:
            await _send(websocket, {"type": "error", "code": "INPUT_TOO_LARGE", "message": "单次输入不能超过 64 KiB"})
            continue
        output, should_exit = terminal.feed(data)
        if output:
            await _send(websocket, {"type": "output", "data": output})
        if should_exit:
            await _send(websocket, {"type": "exit", "exit_code": 0})
            return "shell_exit"


def _remote_command(resource: TerminalResource) -> typing.Union[str, None]:
    if not resource.remote_path.strip():
        return None
    path = shlex.quote(resource.remote_path.strip())
    return (
        f"if cd -- {path} 2>/dev/null; then :; "
        "else printf '\\r\\nOpenSLT: configured remote path is unavailable; using home directory.\\r\\n'; cd ~; fi; "
        'exec "${SHELL:-/bin/sh}" -l'
    )


async def _receive_remote(websocket: WebSocket, process: asyncssh.SSHClientProcess) -> str:
    while True:
        try:
            message = await websocket.receive_json()
        except (WebSocketDisconnect, RuntimeError):
            return "client_disconnected"
        message_type = message.get("type")
        if message_type == "input":
            data = message.get("data")
            if not isinstance(data, str) or len(data.encode("utf-8")) > MAX_INPUT_SIZE:
                await _send(websocket, {"type": "error", "code": "INPUT_TOO_LARGE", "message": "单次输入不能超过 64 KiB"})
                continue
            process.stdin.write(data)
        elif message_type == "resize":
            columns = _clamp(message.get("cols"), MIN_COLUMNS, MAX_COLUMNS, 120)
            rows = _clamp(message.get("rows"), MIN_ROWS, MAX_ROWS, 32)
            process.change_terminal_size(columns, rows)
        else:
            await _send(websocket, {"type": "error", "code": "INVALID_MESSAGE", "message": "不支持的终端消息"})


async def _send_remote_output(websocket: WebSocket, process: asyncssh.SSHClientProcess) -> str:
    while True:
        data = await process.stdout.read(32768)
        if not data:
            await process.wait()
            await _send(websocket, {"type": "exit", "exit_code": process.exit_status})
            return "shell_exit"
        if not await _send(websocket, {"type": "output", "data": data}):
            return "client_disconnected"


async def _run_remote(websocket: WebSocket, resource: TerminalResource, on_connected: typing.Callable[[], None]) -> str:
    options: typing.Dict[str, object] = {
        "host": resource.host,
        "port": resource.port,
        "username": resource.username,
        "known_hosts": None,
        "connect_timeout": 15,
        "keepalive_interval": 30,
        "keepalive_count_max": 3,
    }
    if resource.password:
        options["password"] = resource.password
    if resource.private_key:
        options["client_keys"] = [asyncssh.import_private_key(resource.private_key)]

    connection = await asyncssh.connect(**options)
    process = None
    try:
        process = await connection.create_process(
            _remote_command(resource),
            term_type="xterm-256color",
            term_size=(120, 32),
            encoding="utf-8",
            errors="replace",
        )
        await _send(websocket, {"type": "status", "status": "connected", "mode": "remote", "message": "SSH 已连接"})
        on_connected()
        receiver = asyncio.create_task(_receive_remote(websocket, process))
        sender = asyncio.create_task(_send_remote_output(websocket, process))
        done, pending = await asyncio.wait({receiver, sender}, return_when=asyncio.FIRST_COMPLETED)
        reason = next(iter(done)).result()
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task
        return reason
    finally:
        if process is not None:
            process.close()
            with suppress(Exception):
                await process.wait_closed()
        connection.close()
        with suppress(Exception):
            await connection.wait_closed()


async def handle_resource_terminal(websocket: WebSocket, resource_id: int, token: str) -> None:
    trace_token = trace_id_ctx.set(str(uuid4()))
    started_at = datetime.now(timezone.utc)
    actor_id: typing.Union[int, None] = None
    resource: typing.Union[TerminalResource, None] = None
    opened = False
    reason = "connection_failed"
    try:
        context = _load_context(token, resource_id)
        if context[0] is None:
            message = context[1]
            close_code = 4401 if "凭据" in message else 4403
            await _close(websocket, close_code)
            return
        actor_id, resource = context
        await websocket.accept()
        mode = settings.execution_mode
        await _send(websocket, {"type": "status", "status": "connecting", "mode": mode, "message": "正在建立终端会话"})

        if mode == "simulated":
            await _send(websocket, {"type": "status", "status": "connected", "mode": mode, "message": "安全模拟终端已就绪"})
            opened = True
            _audit(websocket, actor_id, resource.id, "resource.terminal.open", detail={"mode": mode})
            reason = await _receive_simulated(websocket, SimulatedTerminal(resource))
        else:
            def record_open() -> None:
                nonlocal opened
                opened = True
                _audit(websocket, actor_id, resource.id, "resource.terminal.open", detail={"mode": mode})

            try:
                reason = await _run_remote(websocket, resource, record_open)
            except Exception as exc:
                if not opened:
                    _audit(
                        websocket,
                        actor_id,
                        resource.id,
                        "resource.terminal.open",
                        result="failed",
                        detail={"mode": mode, "error_type": type(exc).__name__},
                    )
                else:
                    reason = "session_error"
                code = "SSH_SESSION_FAILED" if opened else "SSH_CONNECTION_FAILED"
                label = "SSH 会话异常" if opened else "SSH 连接失败"
                await _send(websocket, {"type": "error", "code": code, "message": f"{label}：{exc}"})
                await _close(websocket, 4511)
                return

        await _send(websocket, {"type": "status", "status": "closed", "mode": mode, "message": "终端会话已结束"})
        await _close(websocket)
    finally:
        if opened and actor_id is not None and resource is not None:
            duration_ms = max(0, int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000))
            _audit(
                websocket,
                actor_id,
                resource.id,
                "resource.terminal.close",
                detail={"mode": settings.execution_mode, "duration_ms": duration_ms, "reason": reason},
            )
        trace_id_ctx.reset(trace_token)

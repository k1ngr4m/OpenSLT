from __future__ import annotations

import typing
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone

import asyncssh

from app.core.logging import redact


@dataclass
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    started_at: datetime
    finished_at: datetime


class SSHAdapter:
    async def check(self, *, host: str, port: int, username: str, password: typing.Union[str, None] = None, private_key: typing.Union[str, None] = None) -> typing.Dict[str, typing.Union[str, bool]]:
        options = {"host": host, "port": port, "username": username, "known_hosts": None}
        if password:
            options["password"] = password
        if private_key:
            options["client_keys"] = [asyncssh.import_private_key(private_key)]
        async with asyncssh.connect(**options) as connection:
            result = await connection.run("printf OPENSLT_OK", check=True)
            return {"ok": result.stdout == "OPENSLT_OK", "message": "SSH connection successful"}

    async def execute(self, connection: asyncssh.SSHClientConnection, command: str) -> CommandResult:
        started_at = datetime.now(timezone.utc)
        result = await connection.run(command, check=False)
        return CommandResult(
            command=redact(command),
            exit_code=result.exit_status,
            stdout=result.stdout,
            stderr=result.stderr,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
        )

    async def stream(self, connection: asyncssh.SSHClientConnection, command: str) -> typing.AsyncIterator[typing.Tuple[str, str]]:
        process = await connection.create_process(command)
        async for line in process.stdout:
            yield "stdout", line.rstrip("\n")
        async for line in process.stderr:
            yield "stderr", line.rstrip("\n")
        await process.wait()


ssh_adapter = SSHAdapter()


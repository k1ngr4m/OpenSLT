from __future__ import annotations

import re
import shlex
import time
import typing
from contextlib import suppress
from uuid import uuid4

import asyncssh

from app.core.logging import redact
from app.models import Resource, RunStep, ScenarioWorkflowNode
from app.services.workflow_capture import _ssh_options
from app.services.workflow_core import WorkflowError
from app.workflow_node_configs import RemStartupConfig, parse_node_config


def _marker(token: str, kind: str, index: int, exit_code: typing.Optional[str] = None) -> str:
    suffix = "" if exit_code is None else ":" + exit_code
    return f"\x1eOPENSLT_REM:{token}:{kind}:{index}{suffix}\x1f"


def _shared_shell_script(commands: typing.Sequence[str], token: str) -> str:
    lines = []
    for index, command in enumerate(commands, 1):
        begin = _marker(token, "BEGIN", index)
        end_format = _marker(token, "END", index, "%s")
        lines.extend([
            f"printf %s {shlex.quote(begin)}",
            f"printf %s {shlex.quote(begin)} >&2",
            command,
            "openslt_rem_status=$?",
            f"printf {shlex.quote(end_format)} \"$openslt_rem_status\"",
            f"printf {shlex.quote(end_format)} \"$openslt_rem_status\" >&2",
            '[ "$openslt_rem_status" -eq 0 ] || exit "$openslt_rem_status"',
        ])
    return "\n".join(lines) + "\n"


def _stream_segments(
    output: str,
    token: str,
) -> typing.Dict[int, typing.Tuple[str, typing.Optional[int]]]:
    boundary = re.compile(
        re.escape(f"\x1eOPENSLT_REM:{token}:")
        + r"(BEGIN|END):(\d+)(?::(-?\d+))?"
        + re.escape("\x1f")
    )
    matches = list(boundary.finditer(output))
    segments: typing.Dict[int, typing.Tuple[str, typing.Optional[int]]] = {}
    for position, match in enumerate(matches):
        if match.group(1) != "BEGIN":
            continue
        index = int(match.group(2))
        end = next(
            (
                candidate
                for candidate in matches[position + 1 :]
                if candidate.group(1) == "END" and int(candidate.group(2)) == index
            ),
            None,
        )
        if end is not None:
            segments[index] = (output[match.end() : end.start()], int(end.group(3) or 0))
        else:
            next_boundary = matches[position + 1] if position + 1 < len(matches) else None
            segments[index] = (
                output[match.end() : next_boundary.start() if next_boundary else len(output)],
                None,
            )
    return segments


async def execute_rem_startup_node(
    step: RunStep,
    node: ScenarioWorkflowNode,
    run_resources: typing.Dict[str, Resource],
    *,
    command_callback: typing.Optional[typing.Callable[[dict], None]] = None,
) -> dict:
    resource = run_resources.get("rem")
    if not resource or resource.is_deleted or not resource.is_enabled:
        raise WorkflowError("REM_RESOURCE_REQUIRED", "运行资源缺少已启用的 REM 柜台", 409)
    if node.node_type != "rem_startup":
        raise WorkflowError("REM_STARTUP_NODE_REQUIRED", "当前节点不是启动 REM 柜台节点", 400)
    if not resource.remote_path.strip():
        raise WorkflowError("REM_REMOTE_PATH_REQUIRED", "REM 资源未配置远端路径", 409)
    config = typing.cast(
        RemStartupConfig,
        parse_node_config(node.node_type, step.config_snapshot or node.config or {}),
    )
    if not config.commands:
        raise WorkflowError("REM_COMMANDS_REQUIRED", "启动 REM 柜台至少需要一条命令", 409)
    remote_workdir = resource.remote_path.strip().rstrip("/") or "/"

    connection = None
    command_results = []
    started_at = time.monotonic()
    try:
        connection = await asyncssh.connect(**_ssh_options(resource))
        token = uuid4().hex
        shell_command = f"cd {shlex.quote(remote_workdir)} && /bin/sh -s"
        result = await connection.run(
            shell_command,
            input=_shared_shell_script(config.commands, token),
            check=False,
        )
        stdout_segments = _stream_segments(str(result.stdout or ""), token)
        stderr_segments = _stream_segments(str(result.stderr or ""), token)

        for index, command in enumerate(config.commands, 1):
            stdout_segment = stdout_segments.get(index)
            stderr_segment = stderr_segments.get(index)
            if (
                stdout_segment is None
                or stderr_segment is None
                or stdout_segment[1] is None
                or stderr_segment[1] is None
            ):
                shell_exit_code = int(result.exit_status)
                exit_code = shell_exit_code if shell_exit_code != 0 else 1
                command_result = {
                    "index": index,
                    "command": command,
                    "exit_code": exit_code,
                    "stdout": redact(stdout_segment[0] if stdout_segment else "")[:4000],
                    "stderr": redact(stderr_segment[0] if stderr_segment else "")[:4000],
                    "shell_terminated": True,
                }
                command_results.append(command_result)
                if command_callback:
                    command_callback(command_result)
                raise WorkflowError(
                    "REM_SHELL_TERMINATED",
                    f"第 {index} 条 REM 命令导致 Shell 提前终止（Shell 退出码 {shell_exit_code}）",
                    409,
                )

            exit_code = stdout_segment[1]
            if stderr_segment[1] != exit_code:
                raise WorkflowError("REM_COMMAND_PROTOCOL_ERROR", "REM 命令状态标记不一致", 409)
            command_result = {
                "index": index,
                "command": command,
                "exit_code": exit_code,
                "stdout": redact(stdout_segment[0])[:4000],
                "stderr": redact(stderr_segment[0])[:4000],
                "shell_terminated": False,
            }
            command_results.append(command_result)
            if command_callback:
                command_callback(command_result)
            if exit_code != 0:
                detail = (stderr_segment[0] or stdout_segment[0] or "远端命令没有返回错误信息").strip()[:1000]
                raise WorkflowError(
                    "REM_COMMAND_FAILED",
                    f"第 {index} 条 REM 命令失败（退出码 {exit_code}）：{redact(detail)}",
                    409,
                )
    except WorkflowError:
        raise
    except Exception as exc:
        raise WorkflowError("REM_STARTUP_FAILED", f"启动 REM 柜台节点执行失败：{redact(str(exc))}", 409) from exc
    finally:
        if connection:
            connection.close()
            with suppress(Exception):
                await connection.wait_closed()

    return {
        "resource_id": resource.id,
        "resource_name": resource.name,
        "remote_workdir": remote_workdir,
        "commands": command_results,
        "exit_code": 0,
        "duration_ms": int((time.monotonic() - started_at) * 1000),
    }

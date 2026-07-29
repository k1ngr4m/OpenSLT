from __future__ import annotations

import shlex
import time
import typing
from contextlib import suppress

import asyncssh

from app.core.logging import redact
from app.models import Resource, ScenarioWorkflowNode
from app.services.workflow_capture import _ssh_options
from app.services.workflow_core import WorkflowError


REM_STARTUP_COMMANDS = (
    ("./stop_rem.sh", "停止 REM 柜台服务"),
    ("./makeneat.sh", "清理 REM 柜台数据流"),
    ("./start_rem_all.sh", "启动 REM 柜台服务"),
)


async def execute_rem_startup_node(
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
    remote_workdir = resource.remote_path.strip().rstrip("/") or "/"

    connection = None
    command_results = []
    started_at = time.monotonic()
    try:
        connection = await asyncssh.connect(**_ssh_options(resource))
        prefix = f"cd {shlex.quote(remote_workdir)} && "
        for script, label in REM_STARTUP_COMMANDS:
            command = prefix + script
            result = await connection.run(command, check=False)
            command_result = {
                "script": script,
                "command": command,
                "label": label,
                "exit_code": result.exit_status,
                "stdout": redact(str(result.stdout or ""))[:4000],
                "stderr": redact(str(result.stderr or ""))[:4000],
            }
            command_results.append(command_result)
            if command_callback:
                command_callback(command_result)
            if result.exit_status != 0:
                detail = str(result.stderr or result.stdout or "远端命令没有返回错误信息").strip()[:1000]
                raise WorkflowError(
                    "REM_COMMAND_FAILED",
                    f"{label}失败（退出码 {result.exit_status}）：{redact(detail)}",
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

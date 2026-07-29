from __future__ import annotations

import shlex
import time
import typing
from contextlib import suppress

import asyncssh

from app.core.logging import redact
from app.models import Resource, RunStep, ScenarioWorkflowNode
from app.services.market_scripts import MarketScriptError, market_script_service
from app.services.workflow_capture import _ssh_options
from app.services.workflow_core import WorkflowError
from app.workflow_node_configs import MarketStartupConfig, parse_node_config


async def execute_market_startup_node(
    step: RunStep,
    node: ScenarioWorkflowNode,
    run_resources: typing.Dict[str, Resource],
    *,
    command_callback: typing.Optional[typing.Callable[[dict], None]] = None,
) -> dict:
    resource = run_resources.get("market")
    if not resource or resource.is_deleted or not resource.is_enabled:
        raise WorkflowError("MARKET_RESOURCE_REQUIRED", "运行资源缺少已启用的模拟市场", 409)
    if node.node_type != "market_startup":
        raise WorkflowError("MARKET_STARTUP_NODE_REQUIRED", "当前节点不是启动模拟市场节点", 400)
    if not resource.remote_path.strip():
        raise WorkflowError("MARKET_REMOTE_PATH_REQUIRED", "模拟市场资源未配置远端路径", 409)
    config = typing.cast(
        MarketStartupConfig,
        parse_node_config(node.node_type, step.config_snapshot or node.config or {}),
    )
    remote_workdir = resource.remote_path.strip().rstrip("/") or "/"

    connection = None
    command_results = []
    started_at = time.monotonic()
    try:
        connection = await asyncssh.connect(**_ssh_options(resource))
        try:
            details = await market_script_service.read_many(
                resource,
                [item.filename for item in config.scripts],
                connection=connection,
            )
        except MarketScriptError as exc:
            raise WorkflowError(exc.code, exc.message, exc.status_code) from exc
        for selection, detail in zip(config.scripts, details):
            if not detail["executable"]:
                raise WorkflowError(
                    "MARKET_SCRIPT_NOT_EXECUTABLE",
                    f"模拟市场脚本 {selection.filename} 没有执行权限",
                    409,
                )
            if detail["checksum"] != selection.checksum:
                raise WorkflowError(
                    "MARKET_SCRIPT_CHANGED",
                    f"模拟市场脚本 {selection.filename} 已发生变化，请重新发布工作流",
                    409,
                )

        prefix = f"cd {shlex.quote(remote_workdir)} && "
        for selection in config.scripts:
            command = prefix + shlex.quote(f"./{selection.filename}")
            result = await connection.run(command, check=False)
            command_result = {
                "script": selection.filename,
                "command": command,
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
                    "MARKET_COMMAND_FAILED",
                    f"启动模拟市场脚本 {selection.filename} 失败（退出码 {result.exit_status}）：{redact(detail)}",
                    409,
                )
    except WorkflowError:
        raise
    except Exception as exc:
        raise WorkflowError(
            "MARKET_STARTUP_FAILED", f"启动模拟市场节点执行失败：{redact(str(exc))}", 409
        ) from exc
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

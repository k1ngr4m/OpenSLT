from __future__ import annotations

import time

from app.services.rem_execution import execute_rem_startup_node
from app.services.workflow_handlers.base import WorkflowExecutionContext


class RemStartupHandler:
    node_types = ("rem_startup",)
    terminal_kind = None

    async def execute(self, context: WorkflowExecutionContext) -> dict:
        resource = context.resources.get("rem")
        remote_workdir = None
        if resource:
            remote_workdir = resource.remote_path.strip().rstrip("/") or "/"
        started_at = time.monotonic()
        context.step.result_summary = {
            "resource_id": resource.id if resource else None,
            "resource_name": resource.name if resource else None,
            "remote_workdir": remote_workdir,
            "commands": [],
            "exit_code": None,
            "duration_ms": 0,
        }

        def log_command(result: dict) -> None:
            succeeded = result["exit_code"] == 0
            summary = dict(context.step.result_summary or {})
            summary["commands"] = [*(summary.get("commands") or []), result]
            summary["exit_code"] = result["exit_code"]
            summary["duration_ms"] = int((time.monotonic() - started_at) * 1000)
            context.step.result_summary = summary
            context.append_log(
                context.db,
                context.run,
                "rem.command_completed" if succeeded else "rem.command_failed",
                f"第 {result['index']} 条 REM 命令{'完成' if succeeded else '失败'}",
                level="INFO" if succeeded else "ERROR",
                step=context.step,
                source="rem",
                detail=result,
                log_type="remote_command",
            )

        try:
            return await execute_rem_startup_node(
                context.step,
                context.node,
                context.resources,
                command_callback=log_command,
            )
        except Exception:
            context.step.result_summary = {
                **(context.step.result_summary or {}),
                "duration_ms": int((time.monotonic() - started_at) * 1000),
            }
            raise


HANDLERS = (RemStartupHandler(),)

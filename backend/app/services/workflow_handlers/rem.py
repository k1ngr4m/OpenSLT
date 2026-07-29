from __future__ import annotations

from app.services.rem_execution import execute_rem_startup_node
from app.services.workflow_handlers.base import WorkflowExecutionContext


class RemStartupHandler:
    node_types = ("rem_startup",)
    terminal_kind = None

    async def execute(self, context: WorkflowExecutionContext) -> dict:
        def log_command(result: dict) -> None:
            succeeded = result["exit_code"] == 0
            context.append_log(
                context.db,
                context.run,
                "rem.command_completed" if succeeded else "rem.command_failed",
                f"{result['label']}{'完成' if succeeded else '失败'}",
                level="INFO" if succeeded else "ERROR",
                step=context.step,
                source="rem",
                detail={
                    "script": result["script"],
                    "command": result["command"],
                    "exit_code": result["exit_code"],
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                },
                log_type="remote_command",
            )

        return await execute_rem_startup_node(
            context.node,
            context.resources,
            command_callback=log_command,
        )


HANDLERS = (RemStartupHandler(),)

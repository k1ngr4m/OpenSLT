from __future__ import annotations

from app.services import workflows
from app.services.workflow_handlers.base import WorkflowExecutionContext


class ParserHandler:
    node_types = ("parser_parse",)
    terminal_kind = None

    async def execute(self, context: WorkflowExecutionContext) -> dict:
        result = await workflows.execute_parser_node(
            context.db,
            context.run,
            context.step,
            context.node,
            context.resources,
        )
        context.append_log(
            context.db,
            context.run,
            "parser.completed",
            "数据解析完成",
            step=context.step,
            source="parser",
            detail={
                "command": result.get("command"),
                "exit_code": result.get("exit_code"),
                "duration_ms": result.get("duration_ms"),
                "output_files": result.get("output_files"),
            },
            log_type="remote_command",
        )
        return result


HANDLERS = (ParserHandler(),)

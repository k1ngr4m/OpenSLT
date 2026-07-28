from __future__ import annotations

from app.services.statistics_execution import execute_statistics_node
from app.services.workflow_handlers.base import WorkflowExecutionContext


class StatisticsHandler:
    node_types = ("data_statistics",)
    terminal_kind = None

    async def execute(self, context: WorkflowExecutionContext) -> dict:
        result = await execute_statistics_node(
            context.db,
            context.run,
            context.step,
            context.node,
            context.resources,
        )
        context.append_log(
            context.db,
            context.run,
            "statistics.completed",
            "数据统计完成",
            step=context.step,
            source="statistics",
            detail={
                "script": result.get("statistics_script"),
                "input_count": len(
                    ((result.get("statistics_selection") or {}).get("inputs") or [])
                ),
                "duration_ms": result.get("duration_ms"),
                "artifact_id": result.get("statistics_artifact_id"),
            },
            log_type="remote_command",
        )
        return result


HANDLERS = (StatisticsHandler(),)

from __future__ import annotations

from app.services.market_execution import execute_market_startup_node
from app.services.workflow_handlers.base import WorkflowExecutionContext


class MarketStartupHandler:
    node_types = ("market_startup",)
    terminal_kind = "market"

    async def execute(self, context: WorkflowExecutionContext) -> dict:
        def log_command(result: dict) -> None:
            succeeded = result["exit_code"] == 0
            context.append_log(
                context.db,
                context.run,
                "market.command_completed" if succeeded else "market.command_failed",
                f"模拟市场脚本 {result['script']}{'执行完成' if succeeded else '执行失败'}",
                level="INFO" if succeeded else "ERROR",
                step=context.step,
                source="market",
                detail=result,
                log_type="remote_command",
            )

        return await execute_market_startup_node(
            context.step,
            context.node,
            context.resources,
            command_callback=log_command,
        )


HANDLERS = (MarketStartupHandler(),)

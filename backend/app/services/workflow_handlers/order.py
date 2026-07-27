from __future__ import annotations

from app.services import workflows
from app.services.workflow_handlers.base import WorkflowExecutionContext


class OrderPreparationHandler:
    node_types = ("order_preparation",)
    terminal_kind = "order"

    async def execute(self, context: WorkflowExecutionContext) -> dict:
        return await workflows.prepare_order_node(
            context.db,
            context.workflow,
            context.node,
            context.resources,
        )


HANDLERS = (OrderPreparationHandler(),)

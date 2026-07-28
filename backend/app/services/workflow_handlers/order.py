from __future__ import annotations

from app.services import workflows
from app.services.order_sessions import launch_order_session
from app.services.workflow_handlers.base import WorkflowExecutionContext


class OrderPreparationHandler:
    node_types = ("order_preparation",)
    terminal_kind = "order"

    async def execute(self, context: WorkflowExecutionContext) -> dict:
        summary = await workflows.prepare_order_node(
            context.db,
            context.workflow,
            context.node,
            context.resources,
        )
        resource = context.resources["order"]
        session = await launch_order_session(
            resource,
            context.run,
            context.step,
            str(summary["generated_command"]),
            replace=context.step.retry_count > 0,
        )
        return {**summary, **session, "resource_id": resource.id, "resource_name": resource.name}


HANDLERS = (OrderPreparationHandler(),)

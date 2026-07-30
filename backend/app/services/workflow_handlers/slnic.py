from __future__ import annotations

from app.services import workflows
from app.services.workflow_handlers.base import WorkflowExecutionContext


SLNIC_TERMINAL_COMMANDS = {
    "slnic_start_capture": {
        "action": "启动",
        "node_label": "启动 SLNIC",
    },
    "slnic_stop_capture": {
        "action": "关闭",
        "node_label": "关闭 SLNIC",
    },
    "slnic_merge_capture": {
        "action": "合并",
        "node_label": "合并 pcapng",
    },
}


class SlnicHandler:
    node_types = tuple(SLNIC_TERMINAL_COMMANDS)
    terminal_kind = "slnic"

    async def execute(self, context: WorkflowExecutionContext) -> dict:
        return await workflows.execute_slnic_node(
            context.db,
            context.run,
            context.step,
            context.node,
            context.resources,
        )


HANDLERS = (SlnicHandler(),)

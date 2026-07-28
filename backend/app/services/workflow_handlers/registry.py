from __future__ import annotations

import typing

from app.services.workflow_handlers.base import WorkflowExecutionContext, WorkflowNodeHandler
from app.services.workflow_handlers.capture import HANDLERS as CAPTURE_HANDLERS
from app.services.workflow_handlers.order import HANDLERS as ORDER_HANDLERS
from app.services.workflow_handlers.parser import HANDLERS as PARSER_HANDLERS
from app.services.workflow_handlers.slnic import HANDLERS as SLNIC_HANDLERS
from app.services.workflow_handlers.statistics import HANDLERS as STATISTICS_HANDLERS


class WorkflowHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: typing.Dict[str, WorkflowNodeHandler] = {}

    def register(self, handler: WorkflowNodeHandler) -> None:
        for node_type in handler.node_types:
            if node_type in self._handlers:
                raise ValueError(f"workflow handler already registered: {node_type}")
            self._handlers[node_type] = handler

    def get(self, node_type: str) -> WorkflowNodeHandler:
        handler = self._handlers.get(node_type)
        if handler is None:
            from app.services.workflows import WorkflowError

            raise WorkflowError("WORKFLOW_NODE_UNSUPPORTED", f"不支持节点类型 {node_type}", 409)
        return handler

    def find(self, node_type: str) -> typing.Optional[WorkflowNodeHandler]:
        return self._handlers.get(node_type)

    async def execute(self, node_type: str, context: WorkflowExecutionContext) -> dict:
        return await self.get(node_type).execute(context)

    @property
    def node_types(self) -> typing.FrozenSet[str]:
        return frozenset(self._handlers)


registry = WorkflowHandlerRegistry()
for registered_handler in (*CAPTURE_HANDLERS, *ORDER_HANDLERS, *SLNIC_HANDLERS, *PARSER_HANDLERS, *STATISTICS_HANDLERS):
    registry.register(registered_handler)

from __future__ import annotations

import typing
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Resource, RunStep, ScenarioWorkflowNode, ScenarioWorkflowVersion, TestRun, TestScenario


@dataclass(frozen=True)
class WorkflowExecutionContext:
    db: Session
    run: TestRun
    step: RunStep
    node: ScenarioWorkflowNode
    scenario: TestScenario
    workflow: ScenarioWorkflowVersion
    resources: typing.Dict[str, Resource]
    append_log: typing.Callable[..., typing.Any]


class WorkflowNodeHandler(typing.Protocol):
    node_types: typing.Tuple[str, ...]
    terminal_kind: typing.Optional[str]

    async def execute(self, context: WorkflowExecutionContext) -> dict:
        ...

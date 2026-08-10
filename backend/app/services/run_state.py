from __future__ import annotations

import typing
from enum import Enum

from app.models import RunStatusTransition, RunStep, TestRun


class RunStatus(str, Enum):
    DRAFT = "draft"
    RESOURCE_QUEUE = "resource_queue"
    PRECHECK = "precheck"
    AWAITING_WIRING = "awaiting_wiring"
    CAPTURE_VALIDATION = "capture_validation"
    ENVIRONMENT_START = "environment_start"
    ORDER_EXECUTION = "order_execution"
    COLLECTION = "collection"
    COCO_PARSE = "coco_parse"
    STATISTICS = "statistics"
    AWAITING_REVIEW = "awaiting_review"
    AWAITING_STEP_START = "awaiting_step_start"
    RUNNING = "running"
    AWAITING_STEP_COMPLETION = "awaiting_step_completion"
    AWAITING_STEP_RETRY = "awaiting_step_retry"
    PAUSED = "paused"
    COMPLETED = "completed"
    PRECHECK_FAILED = "precheck_failed"
    EXECUTION_FAILED = "execution_failed"
    PARSE_FAILED = "parse_failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.COMPLETED.value,
        RunStatus.PRECHECK_FAILED.value,
        RunStatus.EXECUTION_FAILED.value,
        RunStatus.PARSE_FAILED.value,
        RunStatus.CANCELLED.value,
        RunStatus.TIMED_OUT.value,
    }
)

PAUSABLE_RUN_STATUSES = frozenset(
    {
        RunStatus.RESOURCE_QUEUE.value,
        RunStatus.AWAITING_WIRING.value,
        RunStatus.AWAITING_REVIEW.value,
        RunStatus.AWAITING_STEP_START.value,
        RunStatus.AWAITING_STEP_COMPLETION.value,
        RunStatus.AWAITING_STEP_RETRY.value,
    }
)

_RUN_TRANSITIONS: typing.Dict[str, typing.FrozenSet[str]] = {
    RunStatus.DRAFT.value: frozenset(
        {
            RunStatus.RESOURCE_QUEUE.value,
            RunStatus.PRECHECK_FAILED.value,
            RunStatus.EXECUTION_FAILED.value,
            RunStatus.CANCELLED.value,
        }
    ),
    RunStatus.RESOURCE_QUEUE.value: frozenset(
        {
            RunStatus.PRECHECK.value,
            RunStatus.AWAITING_STEP_START.value,
            RunStatus.COMPLETED.value,
            RunStatus.PRECHECK_FAILED.value,
            RunStatus.EXECUTION_FAILED.value,
            RunStatus.PAUSED.value,
            RunStatus.CANCELLED.value,
        }
    ),
    RunStatus.PRECHECK.value: frozenset(
        {
            RunStatus.AWAITING_WIRING.value,
            RunStatus.PRECHECK_FAILED.value,
            RunStatus.CANCELLED.value,
        }
    ),
    RunStatus.AWAITING_WIRING.value: frozenset(
        {
            RunStatus.CAPTURE_VALIDATION.value,
            RunStatus.EXECUTION_FAILED.value,
            RunStatus.PAUSED.value,
            RunStatus.CANCELLED.value,
        }
    ),
    RunStatus.CAPTURE_VALIDATION.value: frozenset(
        {
            RunStatus.ENVIRONMENT_START.value,
            RunStatus.EXECUTION_FAILED.value,
            RunStatus.CANCELLED.value,
        }
    ),
    RunStatus.ENVIRONMENT_START.value: frozenset(
        {
            RunStatus.ORDER_EXECUTION.value,
            RunStatus.EXECUTION_FAILED.value,
            RunStatus.CANCELLED.value,
        }
    ),
    RunStatus.ORDER_EXECUTION.value: frozenset(
        {RunStatus.COLLECTION.value, RunStatus.EXECUTION_FAILED.value, RunStatus.CANCELLED.value}
    ),
    RunStatus.COLLECTION.value: frozenset(
        {RunStatus.COCO_PARSE.value, RunStatus.EXECUTION_FAILED.value, RunStatus.CANCELLED.value}
    ),
    RunStatus.COCO_PARSE.value: frozenset(
        {
            RunStatus.STATISTICS.value,
            RunStatus.PARSE_FAILED.value,
            RunStatus.EXECUTION_FAILED.value,
            RunStatus.CANCELLED.value,
        }
    ),
    RunStatus.STATISTICS.value: frozenset(
        {
            RunStatus.AWAITING_REVIEW.value,
            RunStatus.EXECUTION_FAILED.value,
            RunStatus.CANCELLED.value,
        }
    ),
    RunStatus.AWAITING_REVIEW.value: frozenset(
        {RunStatus.COMPLETED.value, RunStatus.PAUSED.value, RunStatus.CANCELLED.value}
    ),
    RunStatus.AWAITING_STEP_START.value: frozenset(
        {
            RunStatus.RUNNING.value,
            RunStatus.AWAITING_STEP_COMPLETION.value,
            RunStatus.PAUSED.value,
            RunStatus.CANCELLED.value,
        }
    ),
    RunStatus.RUNNING.value: frozenset(
        {
            RunStatus.AWAITING_STEP_COMPLETION.value,
            RunStatus.AWAITING_STEP_RETRY.value,
            RunStatus.EXECUTION_FAILED.value,
            RunStatus.PARSE_FAILED.value,
            RunStatus.CANCELLED.value,
        }
    ),
    RunStatus.AWAITING_STEP_COMPLETION.value: frozenset(
        {
            RunStatus.AWAITING_STEP_START.value,
            RunStatus.RUNNING.value,
            RunStatus.AWAITING_STEP_RETRY.value,
            RunStatus.COMPLETED.value,
            RunStatus.PAUSED.value,
            RunStatus.CANCELLED.value,
        }
    ),
    RunStatus.AWAITING_STEP_RETRY.value: frozenset(
        {
            RunStatus.RUNNING.value,
            RunStatus.AWAITING_STEP_COMPLETION.value,
            RunStatus.PAUSED.value,
            RunStatus.CANCELLED.value,
        }
    ),
    RunStatus.PAUSED.value: PAUSABLE_RUN_STATUSES | frozenset({RunStatus.CANCELLED.value}),
    RunStatus.PRECHECK_FAILED.value: frozenset({RunStatus.RESOURCE_QUEUE.value}),
    RunStatus.EXECUTION_FAILED.value: frozenset({RunStatus.RESOURCE_QUEUE.value}),
    RunStatus.PARSE_FAILED.value: frozenset({RunStatus.RESOURCE_QUEUE.value}),
    RunStatus.COMPLETED.value: frozenset(),
    RunStatus.CANCELLED.value: frozenset(),
    RunStatus.TIMED_OUT.value: frozenset(),
}

for _status in tuple(_RUN_TRANSITIONS):
    if _status not in TERMINAL_RUN_STATUSES:
        _RUN_TRANSITIONS[_status] = _RUN_TRANSITIONS[_status] | frozenset(
            {RunStatus.TIMED_OUT.value}
        )

_STEP_TRANSITIONS: typing.Dict[str, typing.FrozenSet[str]] = {
    StepStatus.PENDING.value: frozenset(
        {StepStatus.RUNNING.value, StepStatus.WAITING.value, StepStatus.CANCELLED.value}
    ),
    StepStatus.RUNNING.value: frozenset(
        {
            StepStatus.WAITING.value,
            StepStatus.SUCCEEDED.value,
            StepStatus.FAILED.value,
            StepStatus.CANCELLED.value,
        }
    ),
    StepStatus.WAITING.value: frozenset(
        {
            StepStatus.RUNNING.value,
            StepStatus.SUCCEEDED.value,
            StepStatus.FAILED.value,
            StepStatus.CANCELLED.value,
        }
    ),
    StepStatus.FAILED.value: frozenset(
        {
            StepStatus.PENDING.value,
            StepStatus.RUNNING.value,
            StepStatus.WAITING.value,
            StepStatus.CANCELLED.value,
        }
    ),
    StepStatus.SUCCEEDED.value: frozenset(),
    StepStatus.CANCELLED.value: frozenset(),
}


class InvalidStateTransition(ValueError):
    def __init__(self, entity: str, current: str, target: str):
        super().__init__(f"invalid {entity} transition: {current} -> {target}")
        self.entity = entity
        self.current = current
        self.target = target


def _transition(
    entity: typing.Any,
    target: typing.Union[str, Enum],
    transitions: typing.Mapping[str, typing.AbstractSet[str]],
    entity_name: str,
) -> None:
    target_value = target.value if isinstance(target, Enum) else target
    current = entity.status
    if current not in transitions or target_value not in transitions:
        raise InvalidStateTransition(entity_name, current, target_value)
    if current == target_value:
        return
    if target_value not in transitions.get(current, frozenset()):
        raise InvalidStateTransition(entity_name, current, target_value)
    entity.status = target_value


def transition_run(
    run: TestRun,
    target: typing.Union[str, RunStatus],
    *,
    source: str = "service",
    actor_id: typing.Optional[int] = None,
    reason: typing.Optional[str] = None,
) -> None:
    target_value = target.value if isinstance(target, Enum) else target
    current = run.status
    _transition(run, target, _RUN_TRANSITIONS, "run")
    if current == target_value:
        return
    next_version = (run.status_version or 0) + 1
    run.status_version = next_version
    run.status_transitions.append(
        RunStatusTransition(
            from_status=current,
            to_status=target_value,
            status_version=next_version,
            source=source,
            actor_id=actor_id,
            reason=reason,
        )
    )


def transition_step(step: RunStep, target: typing.Union[str, StepStatus]) -> None:
    _transition(step, target, _STEP_TRANSITIONS, "step")

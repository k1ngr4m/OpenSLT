from __future__ import annotations

import pytest

from app.models import RunStep, TestRun as RunModel
from app.services.run_state import (
    TERMINAL_RUN_STATUSES,
    InvalidStateTransition,
    RunStatus,
    transition_run,
    transition_step,
)


def test_run_state_accepts_governed_workflow_and_retry_transitions() -> None:
    run = RunModel(status="draft")
    transition_run(run, "resource_queue")
    transition_run(run, "awaiting_step_start")
    transition_run(run, "running")
    transition_run(run, "awaiting_step_retry")
    transition_run(run, "running")
    transition_run(run, "awaiting_step_completion")
    transition_run(run, "completed")
    assert run.status == "completed"


def test_state_machine_rejects_unknown_or_terminal_transitions() -> None:
    run = RunModel(status="completed")
    with pytest.raises(InvalidStateTransition):
        transition_run(run, "resource_queue")

    step = RunStep(status="pending")
    with pytest.raises(InvalidStateTransition):
        transition_step(step, "succeeded")
    with pytest.raises(InvalidStateTransition):
        transition_step(step, "unknown")


def test_step_state_accepts_retry_and_terminal_dispatch_paths() -> None:
    step = RunStep(status="pending")
    transition_step(step, "running")
    transition_step(step, "failed")
    transition_step(step, "waiting")
    transition_step(step, "succeeded")
    assert step.status == "succeeded"


def test_step_state_allows_waiting_to_running_for_repeated_analysis() -> None:
    """统计节点在人工确认前可以从等待状态再次进入执行状态。"""
    step = RunStep(status="waiting")

    transition_step(step, "running")
    transition_step(step, "waiting")

    assert step.status == "waiting"


def test_paused_run_can_resume_or_cancel() -> None:
    run = RunModel(status="awaiting_step_start")
    transition_run(run, "paused")
    transition_run(run, "awaiting_step_start")
    transition_run(run, "paused")
    transition_run(run, "cancelled")
    assert run.status == "cancelled"


@pytest.mark.parametrize(
    "status",
    [item.value for item in RunStatus if item.value not in TERMINAL_RUN_STATUSES],
)
def test_every_non_terminal_run_can_time_out(status: str) -> None:
    run = RunModel(status=status, status_version=4)
    transition_run(run, "timed_out", source="scheduler", reason="deadline exceeded")
    assert run.status == "timed_out"
    assert run.status_version == 5
    transition = run.status_transitions[-1]
    assert transition.from_status == status
    assert transition.to_status == "timed_out"
    assert transition.source == "scheduler"
    assert transition.reason == "deadline exceeded"

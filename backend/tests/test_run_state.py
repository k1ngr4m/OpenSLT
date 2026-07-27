from __future__ import annotations

import pytest

from app.models import RunStep, TestRun as RunModel
from app.services.run_state import InvalidStateTransition, transition_run, transition_step


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


def test_paused_run_can_resume_or_cancel() -> None:
    run = RunModel(status="awaiting_step_start")
    transition_run(run, "paused")
    transition_run(run, "awaiting_step_start")
    transition_run(run, "paused")
    transition_run(run, "cancelled")
    assert run.status == "cancelled"

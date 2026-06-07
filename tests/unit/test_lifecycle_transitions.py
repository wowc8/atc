"""Tests for explicit lifecycle transition contracts."""

from __future__ import annotations

import pytest

from atc.state.transitions import (
    LifecycleTransitionError,
    TaskAssignmentStatus,
    TaskGraphStatus,
    TransitionReasonCode,
    validate_task_assignment_transition,
    validate_task_graph_transition,
)


def test_task_graph_transition_requires_explicit_retry_bridge() -> None:
    """A retried task must reopen to todo before it can become active again."""
    assert (
        validate_task_graph_transition("tg-1", "done", "todo")
        == TaskGraphStatus.TODO
    )
    with pytest.raises(LifecycleTransitionError) as exc_info:
        validate_task_graph_transition("tg-1", "done", "in_progress")

    detail = exc_info.value.to_detail()
    assert detail["code"] == TransitionReasonCode.INVALID_TRANSITION.value
    assert detail["entity_type"] == "task_graph"
    assert detail["current"] == "done"
    assert detail["target"] == "in_progress"
    assert detail["allowed"] == ["todo"]


def test_task_graph_rejects_unknown_status_with_stable_reason_code() -> None:
    with pytest.raises(LifecycleTransitionError) as exc_info:
        validate_task_graph_transition("tg-1", "todo", "blocked")

    assert exc_info.value.reason_code == TransitionReasonCode.INVALID_STATUS
    assert exc_info.value.to_detail()["allowed"] == [
        status.value for status in sorted(TaskGraphStatus, key=lambda item: item.value)
    ]


def test_task_assignment_terminal_state_is_guarded() -> None:
    with pytest.raises(LifecycleTransitionError) as exc_info:
        validate_task_assignment_transition("assign-1", "done", "working")

    assert exc_info.value.reason_code == TransitionReasonCode.INVALID_TRANSITION
    assert exc_info.value.to_detail()["allowed"] == []


def test_task_assignment_happy_path() -> None:
    assert (
        validate_task_assignment_transition("assign-1", "assigned", "working")
        == TaskAssignmentStatus.WORKING
    )

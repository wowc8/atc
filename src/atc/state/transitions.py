"""Explicit lifecycle transition contracts for durable ATC state.

This module is the common boundary for product-level status transitions that
are persisted in SQLite.  It intentionally contains no database code: callers
fetch the current state, validate here, then persist the accepted target.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class TransitionReasonCode(enum.StrEnum):
    """Stable reason codes returned when a lifecycle transition is rejected."""

    INVALID_STATUS = "invalid_status"
    INVALID_TRANSITION = "invalid_transition"


class TaskGraphStatus(enum.StrEnum):
    """Lifecycle states for task graph entries."""

    TODO = "todo"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    ERROR = "error"


class TaskAssignmentStatus(enum.StrEnum):
    """Lifecycle states for Ace task assignments."""

    ASSIGNED = "assigned"
    WORKING = "working"
    DONE = "done"
    FAILED = "failed"


TASK_GRAPH_TRANSITIONS: dict[TaskGraphStatus, set[TaskGraphStatus]] = {
    TaskGraphStatus.TODO: {TaskGraphStatus.ASSIGNED},
    # Reassigning an already assigned task is an idempotent refresh path.
    TaskGraphStatus.ASSIGNED: {
        TaskGraphStatus.ASSIGNED,
        TaskGraphStatus.IN_PROGRESS,
        TaskGraphStatus.TODO,
        TaskGraphStatus.ERROR,
    },
    # Reassignment from in_progress is an explicit recovery/rebalance path.
    TaskGraphStatus.IN_PROGRESS: {
        TaskGraphStatus.ASSIGNED,
        TaskGraphStatus.REVIEW,
        TaskGraphStatus.DONE,
        TaskGraphStatus.ERROR,
    },
    TaskGraphStatus.REVIEW: {
        TaskGraphStatus.IN_PROGRESS,
        TaskGraphStatus.DONE,
        TaskGraphStatus.ERROR,
    },
    # Retry/reopen paths must bridge through todo before work resumes.
    TaskGraphStatus.DONE: {TaskGraphStatus.TODO},
    TaskGraphStatus.ERROR: {TaskGraphStatus.TODO},
}

TASK_ASSIGNMENT_TRANSITIONS: dict[TaskAssignmentStatus, set[TaskAssignmentStatus]] = {
    TaskAssignmentStatus.ASSIGNED: {
        TaskAssignmentStatus.WORKING,
        TaskAssignmentStatus.FAILED,
    },
    TaskAssignmentStatus.WORKING: {
        TaskAssignmentStatus.DONE,
        TaskAssignmentStatus.FAILED,
    },
    TaskAssignmentStatus.DONE: set(),
    TaskAssignmentStatus.FAILED: set(),
}


@dataclass(slots=True)
class LifecycleTransitionError(ValueError):
    """Structured transition rejection visible to API/Tower/Leader callers."""

    entity_type: str
    entity_id: str
    current: str | None
    target: str
    reason_code: TransitionReasonCode
    allowed: tuple[str, ...]

    def __post_init__(self) -> None:
        allowed = ", ".join(self.allowed) if self.allowed else "none"
        current = self.current if self.current is not None else "<invalid>"
        ValueError.__init__(
            self,
            f"Cannot transition {self.entity_type} {self.entity_id} "
            f"from '{current}' to '{self.target}' "
            f"({self.reason_code.value}; allowed: {allowed})",
        )

    def to_detail(self) -> dict[str, object]:
        """Return a stable API error payload."""
        return {
            "code": self.reason_code.value,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "current": self.current,
            "target": self.target,
            "allowed": list(self.allowed),
            "message": str(self),
        }


def _coerce_status(
    enum_type: type[TaskGraphStatus] | type[TaskAssignmentStatus],
    value: str,
    *,
    entity_type: str,
    entity_id: str,
) -> TaskGraphStatus | TaskAssignmentStatus:
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = tuple(sorted(item.value for item in enum_type))
        raise LifecycleTransitionError(
            entity_type=entity_type,
            entity_id=entity_id,
            current=None,
            target=value,
            reason_code=TransitionReasonCode.INVALID_STATUS,
            allowed=allowed,
        ) from exc


def validate_task_graph_transition(
    task_graph_id: str,
    current: str,
    target: str,
) -> TaskGraphStatus:
    """Validate and return the target task-graph status."""
    current_status = _coerce_status(
        TaskGraphStatus,
        current,
        entity_type="task_graph",
        entity_id=task_graph_id,
    )
    target_status = _coerce_status(
        TaskGraphStatus,
        target,
        entity_type="task_graph",
        entity_id=task_graph_id,
    )
    assert isinstance(current_status, TaskGraphStatus)
    assert isinstance(target_status, TaskGraphStatus)
    allowed = TASK_GRAPH_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise LifecycleTransitionError(
            entity_type="task_graph",
            entity_id=task_graph_id,
            current=current_status.value,
            target=target_status.value,
            reason_code=TransitionReasonCode.INVALID_TRANSITION,
            allowed=tuple(sorted(item.value for item in allowed)),
        )
    return target_status


def validate_task_assignment_transition(
    assignment_id: str,
    current: str,
    target: str,
) -> TaskAssignmentStatus:
    """Validate and return the target task-assignment status."""
    current_status = _coerce_status(
        TaskAssignmentStatus,
        current,
        entity_type="task_assignment",
        entity_id=assignment_id,
    )
    target_status = _coerce_status(
        TaskAssignmentStatus,
        target,
        entity_type="task_assignment",
        entity_id=assignment_id,
    )
    assert isinstance(current_status, TaskAssignmentStatus)
    assert isinstance(target_status, TaskAssignmentStatus)
    allowed = TASK_ASSIGNMENT_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise LifecycleTransitionError(
            entity_type="task_assignment",
            entity_id=assignment_id,
            current=current_status.value,
            target=target_status.value,
            reason_code=TransitionReasonCode.INVALID_TRANSITION,
            allowed=tuple(sorted(item.value for item in allowed)),
        )
    return target_status

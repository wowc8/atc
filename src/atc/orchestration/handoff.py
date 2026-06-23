"""Provider-neutral managed-agent handoff contract.

This module intentionally contains no provider prompt matching or tmux behavior.
It normalizes what ATC can prove about parent→child handoffs such as
Tower→Leader kickoff and Leader→Ace assignment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from atc.runtime.models import (
    BlockerReason,
    DeliveryState,
    RoleKind,
    RuntimeDeliveryResult,
    RuntimeState,
)


class HandoffPayloadKind(StrEnum):
    """Canonical payload kinds sent between managed agents."""

    LEADER_GOAL = "leader_goal"
    ACE_ASSIGNMENT = "ace_assignment"


class HandoffLifecycleState(StrEnum):
    """Provider-neutral parent→child handoff lifecycle ladder."""

    NOT_STARTED = "not_started"
    SESSION_CREATED = "session_created"
    STARTUP_INSPECTED = "startup_inspected"
    INPUT_READY = "input_ready"
    PAYLOAD_WRITTEN = "payload_written"
    PAYLOAD_SUBMITTED = "payload_submitted"
    CHILD_REPORTED_ACTIVE = "child_reported_active"
    FIRST_ACTIONABLE_STEP_OBSERVED = "first_actionable_step_observed"
    HANDOFF_VERIFIED = "handoff_verified"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(slots=True)
class ManagedAgentHandoffContext:
    """Shared Tower→Leader and Leader→Ace handoff truth.

    The context is additive: callers can include it in API responses or persist it
    in existing context JSON without requiring a schema migration. It must only
    contain provider-neutral states and reason codes.
    """

    parent_role: RoleKind
    child_role: RoleKind
    payload_kind: HandoffPayloadKind
    lifecycle_state: HandoffLifecycleState
    project_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    assignment_id: str | None = None
    payload_hash: str | None = None
    runtime_state: RuntimeState | None = None
    delivery_state: DeliveryState | None = None
    startup_readiness_state: str | None = None
    blocker_reason: BlockerReason | str | None = None
    child_reported_active: bool = False
    payload_written: bool = False
    payload_submitted: bool = False
    first_actionable_step_observed: bool = False
    handoff_verified: bool = False
    trace_id: str | None = None
    verified_at: str | None = None
    last_activity_at: str | None = None
    recovery_recommendation: dict[str, Any] | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return API-safe provider-neutral handoff truth."""

        blocker = (
            self.blocker_reason.value
            if isinstance(self.blocker_reason, StrEnum)
            else self.blocker_reason
        )
        data: dict[str, Any] = {
            "parent_role": self.parent_role.value,
            "child_role": self.child_role.value,
            "payload_kind": self.payload_kind.value,
            "lifecycle_state": self.lifecycle_state.value,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "assignment_id": self.assignment_id,
            "payload_hash": self.payload_hash,
            "runtime_state": self.runtime_state.value if self.runtime_state else None,
            "delivery_state": self.delivery_state.value if self.delivery_state else None,
            "startup_readiness_state": self.startup_readiness_state,
            "blocker_reason": blocker,
            "child_reported_active": self.child_reported_active,
            "payload_written": self.payload_written,
            "payload_submitted": self.payload_submitted,
            "first_actionable_step_observed": self.first_actionable_step_observed,
            "handoff_verified": self.handoff_verified,
            "trace_id": self.trace_id,
            "verified_at": self.verified_at,
            "last_activity_at": self.last_activity_at,
            "recovery_recommendation": self.recovery_recommendation,
            "evidence": self.evidence,
        }
        return {key: value for key, value in data.items() if value is not None and value != {}}


def _runtime_created(
    runtime_state: RuntimeState | None,
    delivery_state: DeliveryState | None,
) -> bool:
    return delivery_state in {
        DeliveryState.RUNTIME_CREATED,
        DeliveryState.PROMPT_VISIBLE,
        DeliveryState.PAYLOAD_WRITTEN,
        DeliveryState.SUBMIT_SENT,
        DeliveryState.SUBMITTED_PENDING_ACCEPTANCE,
        DeliveryState.ACCEPTED_ACTIVE,
        DeliveryState.BLOCKED,
    } or runtime_state in {
        RuntimeState.READY,
        RuntimeState.ACTIVE,
        RuntimeState.BLOCKED,
        RuntimeState.IDLE,
        RuntimeState.IDLE_AT_DEFAULT_PROMPT,
    }


def _payload_written(delivery_state: DeliveryState | None) -> bool:
    return delivery_state in {
        DeliveryState.PAYLOAD_WRITTEN,
        DeliveryState.SUBMIT_SENT,
        DeliveryState.SUBMITTED_PENDING_ACCEPTANCE,
        DeliveryState.ACCEPTED_ACTIVE,
    }


def _payload_submitted(delivery_state: DeliveryState | None) -> bool:
    return delivery_state in {
        DeliveryState.SUBMIT_SENT,
        DeliveryState.SUBMITTED_PENDING_ACCEPTANCE,
        DeliveryState.ACCEPTED_ACTIVE,
    }


def _startup_readiness_state(
    runtime_state: RuntimeState | None,
    delivery_state: DeliveryState | None,
    blocker: BlockerReason | None,
) -> str:
    if blocker is not None or delivery_state is DeliveryState.BLOCKED:
        return "blocked"
    if runtime_state in {RuntimeState.FAILED, RuntimeState.MISSING, RuntimeState.STALE}:
        return "failed"
    if runtime_state in {
        RuntimeState.READY,
        RuntimeState.ACTIVE,
        RuntimeState.IDLE,
        RuntimeState.IDLE_AT_DEFAULT_PROMPT,
    }:
        return "input_ready"
    if _runtime_created(runtime_state, delivery_state):
        return "startup_inspected"
    return "not_started"


def lifecycle_from_truth(
    *,
    runtime_state: RuntimeState | None,
    delivery_state: DeliveryState | None,
    blocker_reason: BlockerReason | None = None,
    child_reported_active: bool = False,
    first_actionable_step_observed: bool = False,
    handoff_verified: bool = False,
    failed: bool = False,
) -> HandoffLifecycleState:
    """Classify neutral runtime/delivery/report truth into the shared ladder."""

    if blocker_reason is not None or delivery_state is DeliveryState.BLOCKED:
        return HandoffLifecycleState.BLOCKED
    if failed or runtime_state in {RuntimeState.FAILED, RuntimeState.MISSING, RuntimeState.STALE}:
        return HandoffLifecycleState.FAILED
    if handoff_verified:
        return HandoffLifecycleState.HANDOFF_VERIFIED
    if first_actionable_step_observed:
        return HandoffLifecycleState.FIRST_ACTIONABLE_STEP_OBSERVED
    if child_reported_active:
        return HandoffLifecycleState.CHILD_REPORTED_ACTIVE
    if _payload_submitted(delivery_state):
        return HandoffLifecycleState.PAYLOAD_SUBMITTED
    if _payload_written(delivery_state):
        return HandoffLifecycleState.PAYLOAD_WRITTEN
    if runtime_state in {
        RuntimeState.READY,
        RuntimeState.ACTIVE,
        RuntimeState.IDLE,
        RuntimeState.IDLE_AT_DEFAULT_PROMPT,
    }:
        return HandoffLifecycleState.INPUT_READY
    if delivery_state is DeliveryState.PROMPT_VISIBLE:
        return HandoffLifecycleState.STARTUP_INSPECTED
    if _runtime_created(runtime_state, delivery_state):
        return HandoffLifecycleState.SESSION_CREATED
    return HandoffLifecycleState.NOT_STARTED


def handoff_from_delivery_result(
    result: RuntimeDeliveryResult | None,
    *,
    parent_role: RoleKind,
    child_role: RoleKind,
    payload_kind: HandoffPayloadKind,
    project_id: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    assignment_id: str | None = None,
    payload_hash: str | None = None,
    child_reported_active: bool = False,
    first_actionable_step_observed: bool = False,
    verified_at: str | None = None,
    recovery_recommendation: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> ManagedAgentHandoffContext:
    """Build a shared handoff context from runtime delivery truth."""

    runtime_state = result.runtime_state if result else None
    delivery_state = result.delivery_state if result else None
    blocker = result.blocker_reason if result else None
    failed = bool(result and result.status == "failed")
    submitted = _payload_submitted(delivery_state)
    handoff_verified = bool(
        submitted
        and child_reported_active
        and first_actionable_step_observed
        and not blocker
        and not failed
    )
    lifecycle_state = lifecycle_from_truth(
        runtime_state=runtime_state,
        delivery_state=delivery_state,
        blocker_reason=blocker,
        child_reported_active=child_reported_active,
        first_actionable_step_observed=first_actionable_step_observed,
        handoff_verified=handoff_verified,
        failed=failed,
    )
    return ManagedAgentHandoffContext(
        parent_role=parent_role,
        child_role=child_role,
        payload_kind=payload_kind,
        lifecycle_state=lifecycle_state,
        project_id=project_id,
        session_id=session_id or (result.session_id if result else None),
        task_id=task_id,
        assignment_id=assignment_id,
        payload_hash=payload_hash,
        runtime_state=runtime_state,
        delivery_state=delivery_state,
        startup_readiness_state=_startup_readiness_state(runtime_state, delivery_state, blocker),
        blocker_reason=blocker,
        child_reported_active=child_reported_active,
        payload_written=_payload_written(delivery_state),
        payload_submitted=submitted,
        first_actionable_step_observed=first_actionable_step_observed,
        handoff_verified=handoff_verified,
        trace_id=result.trace_id if result else None,
        verified_at=verified_at if handoff_verified else None,
        last_activity_at=result.last_activity_at if result else None,
        recovery_recommendation=recovery_recommendation,
        evidence=evidence or {},
    )


def handoff_from_assignment(
    assignment: Any | None,
    *,
    project_id: str | None = None,
    task_id: str | None = None,
) -> ManagedAgentHandoffContext:
    """Build Leader→Ace handoff truth from a task assignment row/model."""

    delivery_state: DeliveryState | None = None
    blocker: str | None = None
    if assignment is not None:
        raw_delivery_state = getattr(assignment, "dispatch_delivery_state", None)
        if raw_delivery_state:
            try:
                delivery_state = DeliveryState(raw_delivery_state)
            except ValueError:
                delivery_state = DeliveryState.FAILED
        blocker = assignment.blocker_reason
    child_reported_active = bool(assignment and assignment.ace_reported_active)
    first_actionable = bool(assignment and assignment.assignment_accepted)
    verified = bool(assignment and assignment.dispatch_verified and assignment.assignment_accepted)
    lifecycle_state = lifecycle_from_truth(
        runtime_state=None,
        delivery_state=delivery_state,
        blocker_reason=None,
        child_reported_active=child_reported_active,
        first_actionable_step_observed=first_actionable,
        handoff_verified=verified,
        failed=delivery_state is DeliveryState.FAILED,
    )
    if blocker:
        lifecycle_state = HandoffLifecycleState.BLOCKED
    return ManagedAgentHandoffContext(
        parent_role=RoleKind.LEADER,
        child_role=RoleKind.ACE,
        payload_kind=HandoffPayloadKind.ACE_ASSIGNMENT,
        lifecycle_state=lifecycle_state,
        project_id=project_id,
        session_id=assignment.ace_session_id if assignment is not None else None,
        task_id=task_id or (assignment.task_graph_id if assignment is not None else None),
        assignment_id=assignment.assignment_id if assignment is not None else None,
        delivery_state=delivery_state,
        startup_readiness_state=(
            assignment.startup_readiness_state if assignment is not None else "not_started"
        ),
        blocker_reason=blocker,
        child_reported_active=child_reported_active,
        payload_written=delivery_state
        in {
            DeliveryState.PAYLOAD_WRITTEN,
            DeliveryState.SUBMIT_SENT,
            DeliveryState.SUBMITTED_PENDING_ACCEPTANCE,
            DeliveryState.ACCEPTED_ACTIVE,
        },
        payload_submitted=delivery_state
        in {
            DeliveryState.SUBMIT_SENT,
            DeliveryState.SUBMITTED_PENDING_ACCEPTANCE,
            DeliveryState.ACCEPTED_ACTIVE,
        },
        first_actionable_step_observed=first_actionable,
        handoff_verified=verified,
        verified_at=(
            assignment.assignment_accepted_at if verified and assignment is not None else None
        ),
        last_activity_at=assignment.last_activity_at if assignment is not None else None,
        evidence={
            "assignment_status": assignment.status if assignment is not None else None,
            "dispatch_verified": assignment.dispatch_verified if assignment is not None else False,
            "assignment_accepted": assignment.assignment_accepted
            if assignment is not None
            else False,
            "artifact_ready": assignment.artifact_ready if assignment is not None else False,
        }
        if assignment is not None
        else {},
    )

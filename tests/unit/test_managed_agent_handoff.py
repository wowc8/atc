"""Tests for shared managed-agent handoff contract."""

from __future__ import annotations

from types import SimpleNamespace

from atc.orchestration.handoff import (
    HandoffLifecycleState,
    HandoffPayloadKind,
    handoff_from_assignment,
    handoff_from_delivery_result,
)
from atc.runtime.models import (
    BlockerReason,
    DeliveryState,
    RoleKind,
    RuntimeDeliveryResult,
    RuntimeState,
)


def test_handoff_ladder_distinguishes_session_from_verified_leader_handoff() -> None:
    result = RuntimeDeliveryResult(
        session_id="leader-1",
        provider_name="codex",
        role=RoleKind.LEADER,
        status="accepted",
        runtime_state=RuntimeState.ACTIVE,
        delivery_state=DeliveryState.SUBMIT_SENT,
        trace_id="trace-1",
    )

    pending = handoff_from_delivery_result(
        result,
        parent_role=RoleKind.TOWER,
        child_role=RoleKind.LEADER,
        payload_kind=HandoffPayloadKind.LEADER_GOAL,
        project_id="project-1",
    )

    assert pending.lifecycle_state is HandoffLifecycleState.PAYLOAD_SUBMITTED
    assert pending.handoff_verified is False
    assert pending.payload_submitted is True
    assert pending.child_reported_active is False

    verified = handoff_from_delivery_result(
        result,
        parent_role=RoleKind.TOWER,
        child_role=RoleKind.LEADER,
        payload_kind=HandoffPayloadKind.LEADER_GOAL,
        project_id="project-1",
        child_reported_active=True,
        first_actionable_step_observed=True,
        verified_at="2026-06-23T00:00:00+00:00",
    )

    assert verified.lifecycle_state is HandoffLifecycleState.HANDOFF_VERIFIED
    assert verified.handoff_verified is True
    assert verified.verified_at == "2026-06-23T00:00:00+00:00"
    assert verified.as_dict()["payload_kind"] == "leader_goal"


def test_handoff_ladder_surfaces_provider_neutral_blocker() -> None:
    result = RuntimeDeliveryResult(
        session_id="leader-1",
        provider_name="codex",
        role=RoleKind.LEADER,
        status="blocked",
        runtime_state=RuntimeState.BLOCKED,
        delivery_state=DeliveryState.BLOCKED,
        blocker_reason=BlockerReason.RUNTIME_TRUST_REQUIRED,
    )

    handoff = handoff_from_delivery_result(
        result,
        parent_role=RoleKind.TOWER,
        child_role=RoleKind.LEADER,
        payload_kind=HandoffPayloadKind.LEADER_GOAL,
    )

    assert handoff.lifecycle_state is HandoffLifecycleState.BLOCKED
    assert handoff.startup_readiness_state == "blocked"
    assert handoff.as_dict()["blocker_reason"] == "runtime_trust_required"


def test_assignment_handoff_uses_same_lifecycle_contract() -> None:
    assignment = SimpleNamespace(
        ace_session_id="ace-1",
        task_graph_id="task-1",
        assignment_id="assignment-1",
        status="working",
        startup_readiness_state="input_ready",
        dispatch_delivery_state="accepted_active",
        dispatch_verified=True,
        ace_reported_active=True,
        assignment_accepted=True,
        assignment_accepted_at="2026-06-23T00:05:00+00:00",
        acceptance_message="accepted",
        artifact_ready=False,
        blocker_reason=None,
        last_activity_at="2026-06-23T00:05:30+00:00",
    )

    handoff = handoff_from_assignment(assignment, project_id="project-1")

    assert handoff.parent_role is RoleKind.LEADER
    assert handoff.child_role is RoleKind.ACE
    assert handoff.payload_kind is HandoffPayloadKind.ACE_ASSIGNMENT
    assert handoff.lifecycle_state is HandoffLifecycleState.HANDOFF_VERIFIED
    assert handoff.handoff_verified is True
    assert handoff.child_reported_active is True
    assert handoff.first_actionable_step_observed is True
    assert handoff.as_dict()["assignment_id"] == "assignment-1"

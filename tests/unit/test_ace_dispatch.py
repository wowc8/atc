"""Tests for provider-neutral Ace dispatch verification."""

from __future__ import annotations

from atc.leader.dispatch import verify_ace_dispatch_delivery
from atc.runtime.models import (
    BlockerReason,
    DeliveryState,
    RoleKind,
    RuntimeDeliveryResult,
    RuntimeState,
)


def _result(
    *,
    status: str,
    runtime_state: RuntimeState | None,
    delivery_state: DeliveryState | None,
    blocker_reason: BlockerReason | None = None,
) -> RuntimeDeliveryResult:
    return RuntimeDeliveryResult(
        session_id="ace-1",
        provider_name="codex",
        role=RoleKind.ACE,
        status=status,
        runtime_state=runtime_state,
        delivery_state=delivery_state,
        blocker_reason=blocker_reason,
    )


def test_accepted_active_dispatch_is_verified() -> None:
    verification = verify_ace_dispatch_delivery(
        _result(
            status="confirmed",
            runtime_state=RuntimeState.ACTIVE,
            delivery_state=DeliveryState.ACCEPTED_ACTIVE,
        ),
        task_graph_id="task-1",
    )

    assert verification.dispatch_verified is True
    assert verification.dispatch_delivery_state == "accepted_active"
    assert verification.provider_accepted is True
    assert verification.ace_began_work is True
    assert verification.assigned_task_id == "task-1"


def test_submitted_pending_acceptance_is_not_working() -> None:
    verification = verify_ace_dispatch_delivery(
        _result(
            status="accepted",
            runtime_state=RuntimeState.READY,
            delivery_state=DeliveryState.SUBMITTED_PENDING_ACCEPTANCE,
        ),
        task_graph_id="task-1",
    )

    assert verification.dispatch_verified is False
    assert verification.dispatch_delivery_state == "submitted_pending_acceptance"
    assert verification.submit_sent is True
    assert verification.ace_began_work is False
    assert verification.repair_recommendation is not None


def test_blocked_dispatch_preserves_reason() -> None:
    verification = verify_ace_dispatch_delivery(
        _result(
            status="blocked",
            runtime_state=RuntimeState.BLOCKED,
            delivery_state=DeliveryState.BLOCKED,
            blocker_reason=BlockerReason.DEFAULT_PROMPT_VISIBLE,
        ),
        task_graph_id="task-1",
    )

    assert verification.dispatch_verified is False
    assert verification.dispatch_delivery_state == "blocked"
    assert verification.blocker_reason == "default_prompt_visible"

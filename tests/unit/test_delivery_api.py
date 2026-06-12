"""Tests for API delivery status response helpers."""

from __future__ import annotations

from atc.api.delivery import delivery_response
from atc.runtime.models import DeliveryState, RoleKind, RuntimeDeliveryResult, RuntimeState


def test_delivery_response_normalizes_accepted_to_submitted() -> None:
    result = RuntimeDeliveryResult(
        session_id="session-1",
        provider_name="codex",
        role=RoleKind.ACE,
        status="accepted",
        stage="submit_attempted",
        verdict="accepted",
        reason_code="submit_sent",
    )

    response = delivery_response(result, message="submitted")

    assert response["status"] == "submitted"
    assert response["delivery_state"] == "submitted"
    assert response["delivery"]["status"] == "accepted"


def test_delivery_response_exposes_additive_runtime_truth_fields() -> None:
    result = RuntimeDeliveryResult(
        session_id="session-1",
        provider_name="codex",
        role=RoleKind.LEADER,
        status="delivered",
        stage="confirmed_running",
        verdict="accepted",
        reason_code="session_running",
        runtime_state=RuntimeState.ACTIVE,
        delivery_state=DeliveryState.ACCEPTED_ACTIVE,
        last_activity_at="2026-06-12T00:00:00+00:00",
        last_inspected_at="2026-06-12T00:00:00+00:00",
        provider_diagnostics={"trace_stage": "confirmed_running"},
    )

    response = delivery_response(result)

    assert response["delivery_state"] == "delivered"
    assert response["runtime_state"] == "active"
    assert response["truth_delivery_state"] == "accepted_active"
    assert response["last_activity_at"] == "2026-06-12T00:00:00+00:00"
    assert response["provider_diagnostics"] == {"trace_stage": "confirmed_running"}
    assert response["delivery"]["runtime_state"] == "active"
    assert response["delivery"]["truth_delivery_state"] == "accepted_active"

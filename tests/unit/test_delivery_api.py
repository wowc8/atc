"""Tests for API delivery status response helpers."""

from __future__ import annotations

from atc.api.delivery import delivery_response
from atc.runtime.models import RoleKind, RuntimeDeliveryResult


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

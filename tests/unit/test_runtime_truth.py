"""Tests for provider-neutral runtime truth metadata."""

from __future__ import annotations

from atc.runtime.models import (
    BlockerReason,
    DeliveryState,
    RecoveryState,
    RuntimeState,
)
from atc.runtime.tracing import (
    DeliveryAction,
    DeliveryReasonCode,
    DeliveryStage,
    DeliveryVerdict,
    append_trace_event,
    new_trace_id,
    redact_runtime_value,
    runtime_truth_from_trace_event,
    trace_event,
)


def test_trace_event_updates_runtime_truth_metadata_for_submit_sent() -> None:
    metadata: dict[str, object] = {
        "blocker_reason": "pane_missing",
        "recovery_recommendation": {"state": "restart_required"},
    }
    trace_id = new_trace_id()
    event = trace_event(
        trace_id=trace_id,
        session_id="session-1",
        role="leader",
        provider="codex",
        pane_id="%7",
        action=DeliveryAction.INSTRUCTION,
        stage=DeliveryStage.SUBMIT_ATTEMPTED,
        verdict=DeliveryVerdict.ACCEPTED,
        reason_code=DeliveryReasonCode.SUBMIT_SENT,
        prompt_state_before="ready",
        prompt_state_after="submitted",
    )

    append_trace_event(metadata, event)

    assert metadata["runtime_state"] == RuntimeState.READY.value
    assert metadata["truth_delivery_state"] == DeliveryState.SUBMIT_SENT.value
    assert metadata["delivery_state"] == DeliveryState.SUBMIT_SENT.value
    assert metadata["last_inspected_at"] == event.timestamp
    assert metadata["provider_diagnostics"] == {
        "trace_stage": "submit_attempted",
        "trace_verdict": "accepted",
        "trace_reason_code": "submit_sent",
        "action": "instruction",
        "prompt_state_before": "ready",
        "prompt_state_after": "submitted",
    }
    assert "blocker_reason" not in metadata
    assert "recovery_recommendation" not in metadata


def test_unverified_confirmed_running_is_not_a_blocker() -> None:
    event = trace_event(
        trace_id="trace-1",
        session_id="session-1",
        role="leader",
        provider="codex",
        pane_id="%1",
        action=DeliveryAction.INSTRUCTION,
        stage=DeliveryStage.CONFIRMED_RUNNING,
        verdict=DeliveryVerdict.ACCEPTED,
        reason_code=DeliveryReasonCode.DELIVERY_UNVERIFIED,
    )

    truth = runtime_truth_from_trace_event(event)

    assert truth.runtime_state is RuntimeState.ACTIVE
    assert truth.delivery_state is DeliveryState.ACCEPTED_ACTIVE
    assert truth.blocker_reason is None
    assert truth.recovery_recommendation is None


def test_blocked_trace_maps_to_blocker_and_recovery_recommendation() -> None:
    event = trace_event(
        trace_id="trace-1",
        session_id="session-1",
        role="ace",
        provider="codex",
        pane_id="%9",
        action=DeliveryAction.TASK_ASSIGNMENT,
        stage=DeliveryStage.BLOCKED,
        verdict=DeliveryVerdict.BLOCKED,
        reason_code=DeliveryReasonCode.AUTH_REQUIRED,
        details={"token": "super-secret-token", "visible": "ok"},
    )

    truth = runtime_truth_from_trace_event(event)

    assert truth.runtime_state is RuntimeState.BLOCKED
    assert truth.delivery_state is DeliveryState.BLOCKED
    assert truth.blocker_reason is BlockerReason.RUNTIME_AUTH_REQUIRED
    assert truth.provider_diagnostics["token"] == "[REDACTED]"
    assert truth.provider_diagnostics["visible"] == "ok"
    assert truth.recovery_recommendation is not None
    assert truth.recovery_recommendation.state is RecoveryState.BLOCKED
    assert truth.recovery_recommendation.requires_operator is True


def test_active_trace_records_last_activity() -> None:
    event = trace_event(
        trace_id="trace-1",
        session_id="session-1",
        role="leader",
        provider="codex",
        pane_id="%1",
        action=DeliveryAction.INSTRUCTION,
        stage=DeliveryStage.AGENT_OUTPUT_OBSERVED,
        verdict=DeliveryVerdict.CONFIRMED,
        reason_code=DeliveryReasonCode.AGENT_OUTPUT,
    )

    truth = runtime_truth_from_trace_event(event)

    assert truth.runtime_state is RuntimeState.ACTIVE
    assert truth.delivery_state is DeliveryState.ACCEPTED_ACTIVE
    assert truth.last_activity_at == event.timestamp
    assert truth.last_inspected_at == event.timestamp


def test_secret_redaction_handles_nested_diagnostics_and_excerpt_text() -> None:
    redacted = redact_runtime_value(
        {
            "provider_diagnostics": {
                "api_key": "sk-test",
                "nested": ["Authorization: Bearer abc123", {"password": "pw"}],
                "excerpt": "token=abc123 regular text",
            }
        }
    )

    diagnostics = redacted["provider_diagnostics"]
    assert diagnostics["api_key"] == "[REDACTED]"
    assert diagnostics["nested"][0] == "Authorization: [REDACTED] abc123"
    assert diagnostics["nested"][1]["password"] == "[REDACTED]"
    assert diagnostics["excerpt"] == "token=[REDACTED] regular text"

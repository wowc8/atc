"""Structured delivery trace event helpers for runtime instruction delivery.

Phase 1 keeps these traces additive and in-process/metadata-backed so existing
UI/API callers remain compatible while delivery attempts become observable.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from atc.runtime.models import (
    BlockerReason,
    DeliveryState,
    RecoveryRecommendation,
    RecoveryState,
    RuntimeState,
    RuntimeTruthSnapshot,
)


class DeliveryAction(StrEnum):
    """High-level delivery actions that can be traced."""

    SPAWN = "spawn"
    INSTRUCTION = "instruction"
    TASK_ASSIGNMENT = "task_assignment"


class DeliveryStage(StrEnum):
    """Stable stages for tmux-backed delivery attempts."""

    QUEUED = "queued"
    SPAWN_STARTED = "spawn_started"
    SPAWNED = "spawned"
    WRITE_STARTED = "write_started"
    WRITTEN_TO_PTY = "written_to_pty"
    SUBMIT_ATTEMPTED = "submit_attempted"
    PROMPT_CLEARED = "prompt_cleared"
    AGENT_OUTPUT_OBSERVED = "agent_output_observed"
    CONFIRMED_RUNNING = "confirmed_running"
    BLOCKED = "blocked"
    FAILED = "failed"


class DeliveryVerdict(StrEnum):
    """Stable verdict vocabulary for delivery trace events."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    CONFIRMED = "confirmed"
    BLOCKED = "blocked"
    FAILED = "failed"


class DeliveryReasonCode(StrEnum):
    """Stable reason codes attached to trace verdicts."""

    QUEUED = "queued"
    SPAWN_REQUESTED = "spawn_requested"
    PANE_SPAWNED = "pane_spawned"
    PANE_MISSING = "pane_missing"
    EMPTY_PAYLOAD = "empty_payload"
    PTY_WRITE_STARTED = "pty_write_started"
    PTY_WRITE_ACCEPTED = "pty_write_accepted"
    SUBMIT_SENT = "submit_sent"
    PROMPT_STILL_VISIBLE = "prompt_still_visible"
    PROMPT_NOT_READY = "prompt_not_ready"
    AGENT_OUTPUT = "agent_output"
    SESSION_RUNNING = "session_running"
    AUTH_REQUIRED = "auth_required"
    TRUST_REQUIRED = "trust_required"
    PERMISSION_REQUIRED = "permission_required"
    WELCOME_SCREEN = "welcome_screen"
    RUNTIME_UPDATE_REQUIRED = "runtime_update_required"
    PROMPT_NOT_SUBMITTED = "prompt_not_submitted"
    UNKNOWN_PROMPT_BLOCKER = "unknown_prompt_blocker"
    DELIVERY_UNVERIFIED = "delivery_unverified"
    PROVIDER_ERROR = "provider_error"
    UNKNOWN_ERROR = "unknown_error"


_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|auth|authorization|bearer|cookie|credential|password|secret|token)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|password|secret|token)(\s*[:=]\s*)([^\s,;]+)"
)


@dataclass(slots=True)
class DeliveryTraceEvent:
    """A structured event emitted while spawning or instructing a runtime session."""

    trace_id: str
    session_id: str
    role: str
    provider: str
    pane_id: str | None
    action: str
    stage: str
    verdict: str
    reason_code: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    prompt_state_before: str | None = None
    prompt_state_after: str | None = None
    first_output_excerpt: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def new_trace_id() -> str:
    return f"dtr_{uuid4().hex}"


def trace_event(
    *,
    trace_id: str,
    session_id: str,
    role: str,
    provider: str,
    pane_id: str | None,
    action: DeliveryAction | str,
    stage: DeliveryStage | str,
    verdict: DeliveryVerdict | str,
    reason_code: DeliveryReasonCode | str,
    prompt_state_before: str | None = None,
    prompt_state_after: str | None = None,
    first_output_excerpt: str | None = None,
    details: dict[str, Any] | None = None,
) -> DeliveryTraceEvent:
    return DeliveryTraceEvent(
        trace_id=trace_id,
        session_id=session_id,
        role=str(role),
        provider=provider,
        pane_id=pane_id,
        action=action.value if isinstance(action, DeliveryAction) else str(action),
        stage=stage.value if isinstance(stage, DeliveryStage) else str(stage),
        verdict=verdict.value if isinstance(verdict, DeliveryVerdict) else str(verdict),
        reason_code=reason_code.value
        if isinstance(reason_code, DeliveryReasonCode)
        else str(reason_code),
        prompt_state_before=prompt_state_before,
        prompt_state_after=prompt_state_after,
        first_output_excerpt=_trim_excerpt(redact_runtime_value(first_output_excerpt)),
        details=redact_runtime_value(details or {}),
    )


def append_trace_event(metadata: dict[str, Any], event: DeliveryTraceEvent) -> DeliveryTraceEvent:
    """Append a trace event and update additive runtime truth metadata."""

    metadata.setdefault("delivery_trace_id", event.trace_id)
    metadata.setdefault("delivery_trace_events", []).append(event.to_dict())
    truth = runtime_truth_from_trace_event(event)
    metadata["runtime_truth"] = truth.as_dict()
    metadata["runtime_state"] = truth.runtime_state.value
    metadata["truth_delivery_state"] = truth.delivery_state.value
    # Keep the existing ``delivery_state`` key provider-neutral only when no
    # legacy caller has already populated it. Existing API fields remain intact.
    metadata.setdefault("delivery_state", truth.delivery_state.value)
    if truth.blocker_reason is not None:
        metadata["blocker_reason"] = truth.blocker_reason.value
    else:
        metadata.pop("blocker_reason", None)
    metadata["last_inspected_at"] = truth.last_inspected_at
    if truth.last_activity_at is not None:
        metadata["last_activity_at"] = truth.last_activity_at
    metadata["provider_diagnostics"] = truth.provider_diagnostics
    if truth.recovery_recommendation is not None:
        metadata["recovery_recommendation"] = truth.recovery_recommendation.as_dict()
    else:
        metadata.pop("recovery_recommendation", None)
    return event


def runtime_truth_from_trace_event(event: DeliveryTraceEvent) -> RuntimeTruthSnapshot:
    """Build a provider-neutral runtime truth snapshot from one trace event."""

    blocker = _blocker_reason(event.reason_code)
    delivery_state = _delivery_state(event.stage, event.verdict, event.reason_code)
    runtime_state = _runtime_state(event.stage, event.verdict, event.reason_code, blocker)
    recovery = _recovery_recommendation(blocker)
    diagnostics: dict[str, Any] = {
        "trace_stage": event.stage,
        "trace_verdict": event.verdict,
        "trace_reason_code": event.reason_code,
        "action": event.action,
    }
    if event.prompt_state_before is not None:
        diagnostics["prompt_state_before"] = event.prompt_state_before
    if event.prompt_state_after is not None:
        diagnostics["prompt_state_after"] = event.prompt_state_after
    if event.first_output_excerpt is not None:
        diagnostics["first_output_excerpt"] = event.first_output_excerpt
    diagnostics.update(event.details)
    return RuntimeTruthSnapshot(
        runtime_state=runtime_state,
        delivery_state=delivery_state,
        blocker_reason=blocker,
        last_activity_at=event.timestamp
        if delivery_state is DeliveryState.ACCEPTED_ACTIVE
        else None,
        last_inspected_at=event.timestamp,
        provider=event.provider,
        provider_diagnostics=redact_runtime_value(diagnostics),
        recovery_recommendation=recovery,
    )


def redact_runtime_value(value: Any) -> Any:
    """Redact secrets from provider diagnostics before storage or API exposure."""

    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _SECRET_KEY_RE.search(key_text):
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = redact_runtime_value(item)
        return redacted
    if isinstance(value, list):
        return [redact_runtime_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_runtime_value(item) for item in value)
    if isinstance(value, str):
        return _SECRET_VALUE_RE.sub(
            lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value
        )
    return value


def _delivery_state(stage: str, verdict: str, reason_code: str) -> DeliveryState:
    if verdict == DeliveryVerdict.BLOCKED.value or stage == DeliveryStage.BLOCKED.value:
        return DeliveryState.BLOCKED
    if verdict == DeliveryVerdict.FAILED.value or stage == DeliveryStage.FAILED.value:
        return DeliveryState.FAILED
    if stage == DeliveryStage.QUEUED.value:
        return DeliveryState.QUEUED_UNVERIFIED
    if stage in {DeliveryStage.SPAWN_STARTED.value, DeliveryStage.SPAWNED.value}:
        return DeliveryState.RUNTIME_CREATED
    if stage == DeliveryStage.WRITE_STARTED.value:
        return DeliveryState.PROMPT_VISIBLE
    if stage == DeliveryStage.WRITTEN_TO_PTY.value:
        return DeliveryState.PAYLOAD_WRITTEN
    if (
        stage == DeliveryStage.SUBMIT_ATTEMPTED.value
        or reason_code == DeliveryReasonCode.SUBMIT_SENT.value
    ):
        return DeliveryState.SUBMIT_SENT
    if stage in {DeliveryStage.AGENT_OUTPUT_OBSERVED.value, DeliveryStage.CONFIRMED_RUNNING.value}:
        return DeliveryState.ACCEPTED_ACTIVE
    if stage == DeliveryStage.PROMPT_CLEARED.value:
        return DeliveryState.SUBMITTED_PENDING_ACCEPTANCE
    return DeliveryState.SUBMITTED_PENDING_ACCEPTANCE


def _runtime_state(
    stage: str,
    verdict: str,
    reason_code: str,
    blocker: BlockerReason | None,
) -> RuntimeState:
    if reason_code == DeliveryReasonCode.PANE_MISSING.value:
        return RuntimeState.MISSING
    if blocker is not None or verdict == DeliveryVerdict.BLOCKED.value:
        return RuntimeState.BLOCKED
    if verdict == DeliveryVerdict.FAILED.value:
        return RuntimeState.FAILED
    if stage in {DeliveryStage.QUEUED.value, DeliveryStage.SPAWN_STARTED.value}:
        return RuntimeState.STARTING
    if stage == DeliveryStage.SPAWNED.value:
        return RuntimeState.READY
    if stage in {
        DeliveryStage.WRITE_STARTED.value,
        DeliveryStage.WRITTEN_TO_PTY.value,
        DeliveryStage.SUBMIT_ATTEMPTED.value,
        DeliveryStage.PROMPT_CLEARED.value,
    }:
        return RuntimeState.READY
    if stage in {DeliveryStage.AGENT_OUTPUT_OBSERVED.value, DeliveryStage.CONFIRMED_RUNNING.value}:
        return RuntimeState.ACTIVE
    return RuntimeState.IDLE


def _blocker_reason(reason_code: str) -> BlockerReason | None:
    mapping = {
        DeliveryReasonCode.PANE_MISSING.value: BlockerReason.PANE_MISSING,
        DeliveryReasonCode.EMPTY_PAYLOAD.value: BlockerReason.EMPTY_PAYLOAD,
        DeliveryReasonCode.AUTH_REQUIRED.value: BlockerReason.RUNTIME_AUTH_REQUIRED,
        DeliveryReasonCode.TRUST_REQUIRED.value: BlockerReason.RUNTIME_TRUST_REQUIRED,
        DeliveryReasonCode.PERMISSION_REQUIRED.value: BlockerReason.RUNTIME_PERMISSION_REQUIRED,
        DeliveryReasonCode.WELCOME_SCREEN.value: BlockerReason.DEFAULT_PROMPT_VISIBLE,
        DeliveryReasonCode.RUNTIME_UPDATE_REQUIRED.value: BlockerReason.RUNTIME_UPDATE_REQUIRED,
        DeliveryReasonCode.PROMPT_NOT_SUBMITTED.value: BlockerReason.PROMPT_NOT_SUBMITTED,
        DeliveryReasonCode.UNKNOWN_PROMPT_BLOCKER.value: BlockerReason.UNKNOWN_PROMPT_BLOCKER,
        DeliveryReasonCode.PROVIDER_ERROR.value: BlockerReason.PROVIDER_ERROR,
        DeliveryReasonCode.UNKNOWN_ERROR.value: BlockerReason.UNKNOWN_ERROR,
        DeliveryReasonCode.PROMPT_STILL_VISIBLE.value: BlockerReason.PROMPT_NOT_SUBMITTED,
        DeliveryReasonCode.PROMPT_NOT_READY.value: BlockerReason.PROMPT_NOT_SUBMITTED,
    }
    return mapping.get(reason_code)


def _recovery_recommendation(blocker: BlockerReason | None) -> RecoveryRecommendation | None:
    if blocker is None:
        return None
    if blocker in {
        BlockerReason.RUNTIME_AUTH_REQUIRED,
        BlockerReason.RUNTIME_PERMISSION_REQUIRED,
        BlockerReason.UNKNOWN_PROMPT_BLOCKER,
    }:
        return RecoveryRecommendation(
            state=RecoveryState.BLOCKED,
            safety="operator_required",
            message=f"Runtime blocked by {blocker.value}; inspect provider pane before recovery.",
            requires_operator=True,
        )
    if blocker in {BlockerReason.PANE_MISSING, BlockerReason.PROVIDER_ERROR}:
        return RecoveryRecommendation(
            state=RecoveryState.RESTART_REQUIRED,
            safety="inspect_first",
            message=f"Runtime blocked by {blocker.value}; inspect health before restart.",
        )
    return RecoveryRecommendation(
        state=RecoveryState.INSPECTING,
        safety="inspect_first",
        message=f"Runtime blocked by {blocker.value}; inspect before mutating state.",
    )


def _trim_excerpt(excerpt: str | None, *, limit: int = 500) -> str | None:
    if excerpt is None:
        return None
    text = excerpt.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"

"""Structured delivery trace event helpers for runtime instruction delivery.

Phase 1 keeps these traces additive and in-process/metadata-backed so existing
UI/API callers remain compatible while delivery attempts become observable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


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
    DELIVERY_UNVERIFIED = "delivery_unverified"
    PROVIDER_ERROR = "provider_error"
    UNKNOWN_ERROR = "unknown_error"


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
        first_output_excerpt=_trim_excerpt(first_output_excerpt),
        details=details or {},
    )


def append_trace_event(metadata: dict[str, Any], event: DeliveryTraceEvent) -> DeliveryTraceEvent:
    metadata.setdefault("delivery_trace_id", event.trace_id)
    metadata.setdefault("delivery_trace_events", []).append(event.to_dict())
    return event


def _trim_excerpt(excerpt: str | None, *, limit: int = 500) -> str | None:
    if excerpt is None:
        return None
    text = excerpt.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"

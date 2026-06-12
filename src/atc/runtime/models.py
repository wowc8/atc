"""Shared runtime/provider contract models for ATC.

These models define the provider-neutral contract between orchestration,
runtime services, provider implementations, and wrapper integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any


class RoleKind(StrEnum):
    """Canonical runtime role kinds."""

    TOWER = "tower"
    LEADER = "leader"
    ACE = "ace"


class RuntimeTransport(StrEnum):
    """Supported runtime transports."""

    TMUX = "tmux"


class ReadinessState(StrEnum):
    """Normalized readiness states across providers."""

    STARTING = "starting"
    READY = "ready"
    BUSY = "busy"
    BLOCKED = "blocked"
    ERROR = "error"
    STOPPED = "stopped"


class RuntimeBlockReason(StrEnum):
    """Common reasons a provider session may be blocked."""

    LOGIN = "login"
    TRUST = "trust"
    AUTH = "auth"
    PERMISSION = "permission"
    RATE_LIMIT = "rate_limit"
    PROVIDER_PROMPT = "provider_prompt"
    UNKNOWN = "unknown"


class RuntimeState(StrEnum):
    """Provider-neutral runtime truth states.

    These states describe what ATC can prove about the live runtime/pane, not
    whether a product task has been assigned or completed.
    """

    MISSING = "missing"
    STARTING = "starting"
    READY = "ready"
    ACTIVE = "active"
    IDLE = "idle"
    IDLE_AT_DEFAULT_PROMPT = "idle_at_default_prompt"
    BLOCKED = "blocked"
    STALE = "stale"
    COMPLETE = "complete"
    FAILED = "failed"


class DeliveryState(StrEnum):
    """Provider-neutral delivery truth states for prompts/instructions."""

    NOT_STARTED = "not_started"
    QUEUED_UNVERIFIED = "queued_unverified"
    RUNTIME_CREATED = "runtime_created"
    PROMPT_VISIBLE = "prompt_visible"
    PAYLOAD_WRITTEN = "payload_written"
    SUBMIT_SENT = "submit_sent"
    SUBMITTED_PENDING_ACCEPTANCE = "submitted_pending_acceptance"
    ACCEPTED_ACTIVE = "accepted_active"
    BLOCKED = "blocked"
    FAILED = "failed"


class BlockerReason(StrEnum):
    """Stable blocker reason codes shared by API, CLI, UI, and recovery."""

    PANE_MISSING = "pane_missing"
    RUNTIME_UPDATE_REQUIRED = "runtime_update_required"
    RUNTIME_AUTH_REQUIRED = "runtime_auth_required"
    RUNTIME_TRUST_REQUIRED = "runtime_trust_required"
    RUNTIME_PERMISSION_REQUIRED = "runtime_permission_required"
    DEFAULT_PROMPT_VISIBLE = "default_prompt_visible"
    PROMPT_NOT_SUBMITTED = "prompt_not_submitted"
    PROVIDER_ERROR = "provider_error"
    TOOL_SERVER_ERROR = "tool_server_error"
    LEADER_NO_ACTIVITY = "leader_no_activity"
    ACE_DISPATCH_FAILED = "ace_dispatch_failed"
    STALE_AFTER_UPDATE = "stale_after_update"
    UNKNOWN_PROMPT_BLOCKER = "unknown_prompt_blocker"
    DELIVERY_UNVERIFIED = "delivery_unverified"
    EMPTY_PAYLOAD = "empty_payload"
    UNKNOWN_ERROR = "unknown_error"


class RecoveryState(StrEnum):
    """Provider-neutral recovery lifecycle states."""

    NOT_NEEDED = "not_needed"
    QUEUED = "queued"
    INSPECTING = "inspecting"
    RUNTIME_UPDATE_REQUIRED = "runtime_update_required"
    UPDATING = "updating"
    RESTART_REQUIRED = "restart_required"
    RESTARTING = "restarting"
    KICKOFF_RESENDING = "kickoff_resending"
    DISPATCH_RESENDING = "dispatch_resending"
    VERIFYING = "verifying"
    RECOVERED = "recovered"
    BLOCKED = "blocked"
    FAILED = "failed"


class WrapperExitCode(IntEnum):
    """Stable exit codes for the provider CLI wrapper."""

    SUCCESS = 0
    BLOCKED_AUTH = 10
    NOT_READY = 11
    SESSION_MISSING = 12
    INVALID_ARGS = 13
    DELIVERY_FAILED = 20
    RESTORE_FAILED = 21
    INTERNAL_FAILURE = 30


@dataclass(slots=True)
class RuntimeSessionHandle:
    """Normalized runtime handle for a provider-managed session."""

    session_id: str
    provider_name: str
    role: RoleKind
    transport: RuntimeTransport
    project_id: str | None = None
    tmux_session: str | None = None
    tmux_pane: str | None = None
    working_dir: str | None = None
    context_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StartRoleRequest:
    """Request to start a runtime role session."""

    session_id: str
    provider_name: str
    role: RoleKind
    project_id: str | None = None
    connection: Any | None = None
    working_dir: str | None = None
    context_ref: str | None = None
    display_name: str | None = None
    provider_config_ref: str | None = None
    bootstrap_file: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StopRoleRequest:
    """Request to stop a runtime role session."""

    reason: str | None = None
    graceful: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class InstructionRequest:
    """Request to deliver a general instruction to a session."""

    session_id: str
    message: str | None = None
    message_file: str | None = None
    context_ref: str | None = None
    instruction_id: str | None = None
    expects_readiness_check: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.message and not self.message_file:
            raise ValueError("InstructionRequest requires message or message_file")


@dataclass(slots=True)
class TaskAssignmentRequest:
    """Request to assign a task to a session."""

    session_id: str
    task_id: str
    task_title: str | None = None
    message: str | None = None
    message_file: str | None = None
    context_ref: str | None = None
    assignment_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.message and not self.message_file:
            raise ValueError("TaskAssignmentRequest requires message or message_file")


@dataclass(slots=True)
class ReadinessResult:
    """Normalized readiness result from a provider."""

    session_id: str
    provider_name: str
    state: ReadinessState
    block_reason: RuntimeBlockReason | None = None
    summary: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeInspection:
    """Normalized inspection result for a provider-managed session."""

    session_id: str
    provider_name: str
    alive: bool
    readiness: ReadinessState
    block_reason: RuntimeBlockReason | None = None
    summary: str | None = None
    last_output_excerpt: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RecoveryRecommendation:
    """Operator-facing recovery guidance derived from runtime truth."""

    state: RecoveryState
    command: str | None = None
    safety: str = "inspect_first"
    message: str | None = None
    requires_operator: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "command": self.command,
            "safety": self.safety,
            "message": self.message,
            "requires_operator": self.requires_operator,
        }


@dataclass(slots=True)
class RuntimeTruthSnapshot:
    """Additive runtime truth metadata that can be persisted or returned by APIs."""

    runtime_state: RuntimeState
    delivery_state: DeliveryState
    blocker_reason: BlockerReason | None = None
    last_activity_at: str | None = None
    last_inspected_at: str | None = None
    provider: str | None = None
    provider_diagnostics: dict[str, Any] = field(default_factory=dict)
    recovery_recommendation: RecoveryRecommendation | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "runtime_state": self.runtime_state.value,
            "delivery_state": self.delivery_state.value,
            "blocker_reason": self.blocker_reason.value if self.blocker_reason else None,
            "last_activity_at": self.last_activity_at,
            "last_inspected_at": self.last_inspected_at,
            "provider": self.provider,
            "provider_diagnostics": self.provider_diagnostics,
            "recovery_recommendation": self.recovery_recommendation.as_dict()
            if self.recovery_recommendation
            else None,
        }
        return {key: value for key, value in data.items() if value is not None}


@dataclass(slots=True)
class RuntimeDeliveryResult:
    """Provider-neutral result for a runtime delivery attempt."""

    session_id: str
    provider_name: str
    role: RoleKind
    status: str
    stage: str | None = None
    verdict: str | None = None
    reason_code: str | None = None
    trace_id: str | None = None
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    runtime_state: RuntimeState | None = None
    delivery_state: DeliveryState | None = None
    blocker_reason: BlockerReason | None = None
    last_activity_at: str | None = None
    last_inspected_at: str | None = None
    provider_diagnostics: dict[str, Any] = field(default_factory=dict)
    recovery_recommendation: RecoveryRecommendation | None = None

    @property
    def ok(self) -> bool:
        return self.status in {"delivered", "confirmed", "accepted"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "provider": self.provider_name,
            "role": self.role.value,
            "status": self.status,
            "stage": self.stage,
            "verdict": self.verdict,
            "reason_code": self.reason_code,
            "trace_id": self.trace_id,
            "message": self.message,
            "details": self.details,
            "runtime_state": self.runtime_state.value if self.runtime_state else None,
            "truth_delivery_state": self.delivery_state.value if self.delivery_state else None,
            "blocker_reason": self.blocker_reason.value if self.blocker_reason else None,
            "last_activity_at": self.last_activity_at,
            "last_inspected_at": self.last_inspected_at,
            "provider_diagnostics": self.provider_diagnostics,
            "recovery_recommendation": self.recovery_recommendation.as_dict()
            if self.recovery_recommendation
            else None,
        }

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
        }

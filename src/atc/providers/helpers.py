"""Provider-native helper subagent contract.

Provider helper subagents are private/background assistants used by Tower,
Leader, or Ace sessions. They are not ATC command-chain roles; ATC remains the
source of truth for state and audit records while providers own helper execution
mechanics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ProviderHelperVisibility(StrEnum):
    """Display level for provider helper activity."""

    HIDDEN = "hidden"
    SUMMARY = "summary"
    FULL = "full"


class ProviderHelperParentRole(StrEnum):
    """ATC-visible role that requested a helper run."""

    TOWER = "tower"
    LEADER = "leader"
    ACE = "ace"


class ProviderHelperRunStatus(StrEnum):
    """Lifecycle status for a provider helper run audit record."""

    REQUESTED = "requested"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProviderHelperEventType(StrEnum):
    """Known helper event types.

    Providers may persist additional event_type strings as long as they remain
    provider-neutral audit events and do not mutate ATC state directly.
    """

    HELPER_REQUESTED = "helper_requested"
    HELPER_STARTED = "helper_started"
    PROMPT_SUBMITTED = "prompt_submitted"
    PROVIDER_OUTPUT_RECEIVED = "provider_output_received"
    ACTION_REQUESTED = "action_requested"
    ACTION_COMPLETED = "action_completed"
    TOKEN_USAGE_RECORDED = "token_usage_recorded"
    HELPER_COMPLETED = "helper_completed"
    HELPER_FAILED = "helper_failed"


@dataclass(frozen=True)
class ProviderHelperRequest:
    """Provider-neutral request to start a helper subagent.

    Provider modules may translate this request into provider-native helper
    mechanics. The request itself intentionally contains no Codex/Claude-specific
    syntax, event names, or subprocess details.
    """

    provider: str
    parent_session_id: str
    parent_role: ProviderHelperParentRole | str
    purpose: str
    prompt: str
    project_id: str | None = None
    task_id: str | None = None
    helper_id: str | None = None
    visibility: ProviderHelperVisibility | str = ProviderHelperVisibility.HIDDEN
    allowed_tools: tuple[str, ...] = ()
    allowed_actions: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "parent_role", ProviderHelperParentRole(self.parent_role))
        object.__setattr__(self, "visibility", ProviderHelperVisibility(self.visibility))
        object.__setattr__(self, "allowed_tools", tuple(self.allowed_tools))
        object.__setattr__(self, "allowed_actions", tuple(self.allowed_actions))
        if not self.provider.strip():
            raise ValueError("provider is required")
        if not self.parent_session_id.strip():
            raise ValueError("parent_session_id is required")
        if not self.purpose.strip():
            raise ValueError("purpose is required")
        if not self.prompt.strip():
            raise ValueError("prompt is required")


@dataclass(frozen=True)
class ProviderHelperResult:
    """Provider-neutral completion result from a helper subagent."""

    helper_run_id: str
    status: ProviderHelperRunStatus | str
    summary: str | None = None
    output_text: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", ProviderHelperRunStatus(self.status))

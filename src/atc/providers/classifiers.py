"""Provider-owned runtime classification contracts.

Classifiers translate provider-specific terminal observations into ATC's
provider-neutral runtime truth vocabulary. Core Tower/Leader/Ace orchestration
must consume these neutral fields instead of matching provider prompt text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from atc.runtime.models import (
    BlockerReason,
    DeliveryState,
    ReadinessState,
    RecoveryState,
    RuntimeBlockReason,
    RuntimeState,
)


@dataclass(frozen=True, slots=True)
class RecoveryCapabilities:
    """Provider recovery capabilities discovered or declared by a classifier."""

    can_detect_update_prompt: bool = False
    can_accept_update_prompt: bool = False
    requires_fresh_session_after_update: bool = False
    can_detect_default_prompt: bool = False
    can_detect_unsubmitted_prompt: bool = False
    can_detect_auth_prompt: bool = False
    can_detect_trust_prompt: bool = False
    can_detect_permission_prompt: bool = False
    can_classify_trust_prompt: bool = False
    can_auto_accept_managed_workspace_trust_prompt: bool = False
    can_classify_local_api_approval_prompt: bool = False
    can_preauthorize_local_atc_api_access: bool = False
    can_distinguish_auth_secret_unknown_permission_prompts: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "can_detect_update_prompt": self.can_detect_update_prompt,
            "can_accept_update_prompt": self.can_accept_update_prompt,
            "requires_fresh_session_after_update": self.requires_fresh_session_after_update,
            "can_detect_default_prompt": self.can_detect_default_prompt,
            "can_detect_unsubmitted_prompt": self.can_detect_unsubmitted_prompt,
            "can_detect_auth_prompt": self.can_detect_auth_prompt,
            "can_detect_trust_prompt": self.can_detect_trust_prompt,
            "can_detect_permission_prompt": self.can_detect_permission_prompt,
            "can_classify_trust_prompt": self.can_classify_trust_prompt,
            "can_auto_accept_managed_workspace_trust_prompt": (
                self.can_auto_accept_managed_workspace_trust_prompt
            ),
            "can_classify_local_api_approval_prompt": self.can_classify_local_api_approval_prompt,
            "can_preauthorize_local_atc_api_access": self.can_preauthorize_local_atc_api_access,
            "can_distinguish_auth_secret_unknown_permission_prompts": (
                self.can_distinguish_auth_secret_unknown_permission_prompts
            ),
        }


@dataclass(frozen=True, slots=True)
class RuntimeClassification:
    """Provider-neutral classification for one terminal/runtime observation."""

    runtime_state: RuntimeState
    delivery_state: DeliveryState
    readiness: ReadinessState
    block_reason: RuntimeBlockReason | None = None
    blocker_reason: BlockerReason | None = None
    summary: str | None = None
    prompt_state: str | None = None
    provider_observation: str | None = None
    recovery_state: RecoveryState = RecoveryState.NOT_NEEDED
    requires_operator: bool = False
    diagnostics: dict[str, object] = field(default_factory=dict)

    @property
    def is_blocking(self) -> bool:
        return self.blocker_reason is not None or self.readiness in {
            ReadinessState.BLOCKED,
            ReadinessState.ERROR,
            ReadinessState.STOPPED,
        }

    def as_details(self) -> dict[str, object]:
        details: dict[str, object] = {
            "runtime_state": self.runtime_state.value,
            "delivery_state": self.delivery_state.value,
            "readiness": self.readiness.value,
            "recovery_state": self.recovery_state.value,
        }
        if self.block_reason is not None:
            details["block_reason"] = self.block_reason.value
        if self.blocker_reason is not None:
            details["blocker_reason"] = self.blocker_reason.value
        if self.prompt_state is not None:
            details["prompt_state"] = self.prompt_state
        if self.provider_observation is not None:
            details["provider_observation"] = self.provider_observation
        if self.requires_operator:
            details["requires_operator"] = True
        if self.diagnostics:
            details["provider_diagnostics"] = self.diagnostics
        return details


@runtime_checkable
class RuntimeProviderClassifier(Protocol):
    """Provider-owned runtime inspection/classification contract."""

    provider_name: str

    def classify_excerpt(
        self, excerpt: str, *, pane_missing: bool = False
    ) -> RuntimeClassification:
        """Classify a terminal excerpt into ATC provider-neutral runtime truth."""

    def prompt_state_for_excerpt(self, excerpt: str) -> str:
        """Return compact provider-neutral prompt state text for delivery traces."""

    def recovery_capabilities(self) -> RecoveryCapabilities:
        """Return provider recovery capabilities without performing recovery."""

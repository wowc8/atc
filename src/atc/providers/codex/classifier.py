"""Codex runtime classifier.

Codex-specific prompt matching lives here so orchestration, Tower, Leader, Ace,
REST, CLI, and MCP only see provider-neutral runtime truth.
"""

from __future__ import annotations

import re

from atc.providers.classifiers import RecoveryCapabilities, RuntimeClassification
from atc.runtime.interrupts import (
    RuntimeInterruptSpec,
    detect_runtime_interrupt,
    interrupt_prompt_state,
)
from atc.runtime.models import (
    BlockerReason,
    DeliveryState,
    ReadinessState,
    RecoveryState,
    RuntimeBlockReason,
    RuntimeState,
)

CODEX_PROMPT_RE = re.compile(r"(^|\n)\s*(❯|>)\s*$|(^|\n)\s*›\s+(?!\d+\.)", re.MULTILINE)

_AUTH_TRIGGERS = (
    "login",
    "sign in",
    "authentication",
    "api key",
)
_TRUST_TRIGGERS = (
    "trust this folder",
    "do you trust",
    "trust the contents",
)
_PERMISSION_TRIGGERS = (
    "allow command",
    "allow this command",
    "approve command",
    "permission",
)
_PROVIDER_ERROR_TRIGGERS = (
    "failed to start provider",
    "failed to start codex",
    "failed to start claude",
)
_UPDATE_TRIGGERS = (
    "update available",
    "a new version of codex is available",
    "new codex version available",
    "codex update available",
    "update codex",
)
_POST_UPDATE_STALE_TRIGGERS = (
    "codex has been updated",
    "restart codex",
    "please restart codex",
    "relaunch codex",
)
_DEFAULT_PROMPT_TRIGGERS = (
    "implement {feature}",
    "explain this codebase",
)
_UNSUBMITTED_PROMPT_TRIGGERS = (
    "implement ",
    "fix ",
    "add ",
    "update ",
)

_INTERRUPT_SPEC = RuntimeInterruptSpec(
    trust_triggers=_TRUST_TRIGGERS,
    permission_triggers=_PERMISSION_TRIGGERS,
    login_triggers=_AUTH_TRIGGERS,
    provider_error_triggers=_PROVIDER_ERROR_TRIGGERS,
)


class CodexRuntimeClassifier:
    """Classify Codex pane excerpts behind provider-neutral ATC states."""

    provider_name = "codex"

    def classify_excerpt(
        self, excerpt: str, *, pane_missing: bool = False
    ) -> RuntimeClassification:
        if pane_missing:
            return RuntimeClassification(
                runtime_state=RuntimeState.MISSING,
                delivery_state=DeliveryState.FAILED,
                readiness=ReadinessState.STOPPED,
                blocker_reason=BlockerReason.PANE_MISSING,
                summary="Pane missing",
                prompt_state="missing:pane",
                provider_observation="pane_missing",
                recovery_state=RecoveryState.RESTART_REQUIRED,
                requires_operator=True,
            )

        interrupt = self.detect_interrupt(excerpt)
        if interrupt is not None:
            return RuntimeClassification(
                runtime_state=RuntimeState.FAILED
                if interrupt.readiness is ReadinessState.ERROR
                else RuntimeState.BLOCKED,
                delivery_state=DeliveryState.BLOCKED,
                readiness=interrupt.readiness,
                block_reason=interrupt.block_reason,
                blocker_reason=_blocker_reason_for_interrupt(interrupt.reason_code.value),
                summary=interrupt.summary,
                prompt_state=interrupt_prompt_state(interrupt, interrupt.readiness.value),
                provider_observation=interrupt.interrupt_type.value,
                recovery_state=RecoveryState.BLOCKED,
                requires_operator=True,
                diagnostics=interrupt.to_trace_details(),
            )

        lower = excerpt.lower()
        if _contains_any(lower, _UPDATE_TRIGGERS):
            return RuntimeClassification(
                runtime_state=RuntimeState.BLOCKED,
                delivery_state=DeliveryState.BLOCKED,
                readiness=ReadinessState.BLOCKED,
                block_reason=RuntimeBlockReason.PROVIDER_PROMPT,
                blocker_reason=BlockerReason.RUNTIME_UPDATE_REQUIRED,
                summary="Codex runtime update prompt visible",
                prompt_state="blocked:runtime_update_required",
                provider_observation="codex_update_prompt",
                recovery_state=RecoveryState.RUNTIME_UPDATE_REQUIRED,
                requires_operator=True,
                diagnostics={"codex_observation": "update_prompt"},
            )
        if _contains_any(lower, _POST_UPDATE_STALE_TRIGGERS):
            return RuntimeClassification(
                runtime_state=RuntimeState.STALE,
                delivery_state=DeliveryState.FAILED,
                readiness=ReadinessState.STOPPED,
                block_reason=RuntimeBlockReason.PROVIDER_PROMPT,
                blocker_reason=BlockerReason.STALE_AFTER_UPDATE,
                summary="Codex session is stale after update/reload",
                prompt_state="stale:after_update",
                provider_observation="codex_stale_after_update",
                recovery_state=RecoveryState.RESTART_REQUIRED,
                requires_operator=True,
                diagnostics={"codex_observation": "stale_after_update"},
            )
        if _contains_any(lower, _DEFAULT_PROMPT_TRIGGERS):
            return RuntimeClassification(
                runtime_state=RuntimeState.IDLE_AT_DEFAULT_PROMPT,
                delivery_state=DeliveryState.PROMPT_VISIBLE,
                readiness=ReadinessState.READY,
                blocker_reason=BlockerReason.DEFAULT_PROMPT_VISIBLE,
                summary="Codex default starter prompt visible",
                prompt_state="idle_at_default_prompt",
                provider_observation="codex_default_prompt",
                recovery_state=RecoveryState.NOT_NEEDED,
                diagnostics={"codex_observation": "default_prompt"},
            )
        if self._looks_unsubmitted_prompt(excerpt):
            return RuntimeClassification(
                runtime_state=RuntimeState.IDLE,
                delivery_state=DeliveryState.PAYLOAD_WRITTEN,
                readiness=ReadinessState.READY,
                blocker_reason=BlockerReason.PROMPT_NOT_SUBMITTED,
                summary="Codex prompt contains visible unsubmitted text",
                prompt_state="prompt_visible:not_submitted",
                provider_observation="codex_prompt_not_submitted",
                recovery_state=RecoveryState.NOT_NEEDED,
                diagnostics={"codex_observation": "prompt_not_submitted"},
            )
        if CODEX_PROMPT_RE.search(excerpt):
            return RuntimeClassification(
                runtime_state=RuntimeState.READY,
                delivery_state=DeliveryState.PROMPT_VISIBLE,
                readiness=ReadinessState.READY,
                summary="Codex prompt ready",
                prompt_state=ReadinessState.READY.value,
                provider_observation="codex_ready_prompt",
            )
        if excerpt.strip():
            return RuntimeClassification(
                runtime_state=RuntimeState.ACTIVE,
                delivery_state=DeliveryState.ACCEPTED_ACTIVE,
                readiness=ReadinessState.BUSY,
                summary="Codex output active",
                prompt_state=ReadinessState.BUSY.value,
                provider_observation="codex_active_output",
            )
        return RuntimeClassification(
            runtime_state=RuntimeState.STARTING,
            delivery_state=DeliveryState.SUBMITTED_PENDING_ACCEPTANCE,
            readiness=ReadinessState.BUSY,
            summary="Codex runtime starting or awaiting output",
            prompt_state=ReadinessState.BUSY.value,
            provider_observation="codex_no_output_yet",
        )

    def prompt_state_for_excerpt(self, excerpt: str) -> str:
        classification = self.classify_excerpt(excerpt)
        if classification.prompt_state:
            return classification.prompt_state
        if classification.block_reason is not None:
            return f"{classification.readiness.value}:{classification.block_reason.value}"
        return classification.readiness.value

    def recovery_capabilities(self) -> RecoveryCapabilities:
        return RecoveryCapabilities(
            can_detect_update_prompt=True,
            can_accept_update_prompt=False,
            requires_fresh_session_after_update=True,
            can_detect_default_prompt=True,
            can_detect_unsubmitted_prompt=True,
            can_detect_auth_prompt=True,
            can_detect_trust_prompt=True,
            can_detect_permission_prompt=True,
        )

    def detect_interrupt(self, excerpt: str):
        lower = excerpt.lower()
        last_interrupt = max(
            lower.rfind(trigger)
            for trigger in (
                *_TRUST_TRIGGERS,
                *_PERMISSION_TRIGGERS,
                *_AUTH_TRIGGERS,
                *_PROVIDER_ERROR_TRIGGERS,
            )
        )
        last_ready = max(
            lower.rfind(marker)
            for marker in (
                "\n› ",
                "gpt-5.5 default",
                "gpt-5 default",
            )
        )
        if last_interrupt >= 0 and last_ready > last_interrupt:
            return None
        return detect_runtime_interrupt(excerpt, _INTERRUPT_SPEC)

    def _looks_unsubmitted_prompt(self, excerpt: str) -> bool:
        lines = [line.strip().lower() for line in excerpt.splitlines() if line.strip()]
        if not lines:
            return False
        latest = lines[-1]
        if not latest.startswith(("›", ">", "❯")):
            return False
        prompt_text = latest.lstrip("›>❯ ").strip()
        if not prompt_text:
            return False
        return _contains_any(prompt_text, _UNSUBMITTED_PROMPT_TRIGGERS)


def _contains_any(text: str, triggers: tuple[str, ...]) -> bool:
    return any(trigger in text for trigger in triggers)


def _blocker_reason_for_interrupt(reason_code: str) -> BlockerReason:
    if reason_code == "auth_required":
        return BlockerReason.RUNTIME_AUTH_REQUIRED
    if reason_code == "trust_required":
        return BlockerReason.RUNTIME_TRUST_REQUIRED
    if reason_code == "permission_required":
        return BlockerReason.RUNTIME_PERMISSION_REQUIRED
    if reason_code == "provider_error":
        return BlockerReason.PROVIDER_ERROR
    return BlockerReason.UNKNOWN_PROMPT_BLOCKER

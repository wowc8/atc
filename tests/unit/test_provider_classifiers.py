"""Tests for provider-owned runtime classifiers."""

from __future__ import annotations

from atc.providers.classifiers import RuntimeProviderClassifier
from atc.providers.codex.classifier import CodexRuntimeClassifier
from atc.runtime.models import (
    BlockerReason,
    DeliveryState,
    ReadinessState,
    RecoveryState,
    RuntimeBlockReason,
    RuntimeState,
)


def _classifier() -> CodexRuntimeClassifier:
    return CodexRuntimeClassifier()


def test_codex_classifier_satisfies_provider_contract() -> None:
    classifier = _classifier()

    assert isinstance(classifier, RuntimeProviderClassifier)
    assert classifier.provider_name == "codex"

    capabilities = classifier.recovery_capabilities()
    assert capabilities.can_detect_update_prompt is True
    assert capabilities.requires_fresh_session_after_update is True
    assert capabilities.can_detect_default_prompt is True
    assert capabilities.can_detect_unsubmitted_prompt is True


def test_codex_update_prompt_maps_to_neutral_update_blocker() -> None:
    result = _classifier().classify_excerpt(
        "A new version of Codex is available. Update Codex? [y/N]"
    )

    assert result.runtime_state is RuntimeState.BLOCKED
    assert result.delivery_state is DeliveryState.BLOCKED
    assert result.readiness is ReadinessState.BLOCKED
    assert result.block_reason is RuntimeBlockReason.PROVIDER_PROMPT
    assert result.blocker_reason is BlockerReason.RUNTIME_UPDATE_REQUIRED
    assert result.recovery_state is RecoveryState.RUNTIME_UPDATE_REQUIRED
    assert result.provider_observation == "codex_update_prompt"


def test_codex_stale_after_update_maps_to_stale_restart_required() -> None:
    result = _classifier().classify_excerpt("Codex has been updated. Please restart Codex.")

    assert result.runtime_state is RuntimeState.STALE
    assert result.blocker_reason is BlockerReason.STALE_AFTER_UPDATE
    assert result.recovery_state is RecoveryState.RESTART_REQUIRED
    assert result.provider_observation == "codex_stale_after_update"


def test_codex_default_prompt_is_idle_not_active_work() -> None:
    result = _classifier().classify_excerpt("\n› Implement {feature}\n  Explain this codebase")

    assert result.runtime_state is RuntimeState.IDLE_AT_DEFAULT_PROMPT
    assert result.delivery_state is DeliveryState.PROMPT_VISIBLE
    assert result.readiness is ReadinessState.READY
    assert result.blocker_reason is BlockerReason.DEFAULT_PROMPT_VISIBLE
    assert result.provider_observation == "codex_default_prompt"


def test_codex_visible_unsubmitted_prompt_is_not_submitted() -> None:
    result = _classifier().classify_excerpt("\n› Review PR #287")

    assert result.runtime_state is RuntimeState.IDLE
    assert result.delivery_state is DeliveryState.PAYLOAD_WRITTEN
    assert result.blocker_reason is BlockerReason.PROMPT_NOT_SUBMITTED
    assert result.prompt_state == "prompt_visible:not_submitted"


def test_codex_ready_prompt_is_prompt_visible_without_blocker() -> None:
    result = _classifier().classify_excerpt("\nmodel: gpt-5.5 default\n› ")

    assert result.runtime_state is RuntimeState.READY
    assert result.delivery_state is DeliveryState.PROMPT_VISIBLE
    assert result.blocker_reason is None
    assert result.prompt_state == "ready"


def test_codex_active_output_maps_to_accepted_active() -> None:
    result = _classifier().classify_excerpt(
        "Thinking...\nI need inspect the repository and run tests."
    )

    assert result.runtime_state is RuntimeState.ACTIVE
    assert result.delivery_state is DeliveryState.ACCEPTED_ACTIVE
    assert result.readiness is ReadinessState.BUSY
    assert result.provider_observation == "codex_active_output"


def test_codex_missing_pane_maps_to_pane_missing() -> None:
    result = _classifier().classify_excerpt("", pane_missing=True)

    assert result.runtime_state is RuntimeState.MISSING
    assert result.delivery_state is DeliveryState.FAILED
    assert result.blocker_reason is BlockerReason.PANE_MISSING
    assert result.recovery_state is RecoveryState.RESTART_REQUIRED
    assert result.requires_operator is True


def test_codex_auth_trust_permission_and_provider_error_are_neutral_blockers() -> None:
    cases = [
        ("Sign in to continue", BlockerReason.RUNTIME_AUTH_REQUIRED, RuntimeBlockReason.AUTH),
        (
            "Do you trust this folder?",
            BlockerReason.RUNTIME_TRUST_REQUIRED,
            RuntimeBlockReason.TRUST,
        ),
        (
            "Allow command to run before continuing",
            BlockerReason.RUNTIME_PERMISSION_REQUIRED,
            RuntimeBlockReason.PERMISSION,
        ),
        ("Failed to start Codex", BlockerReason.PROVIDER_ERROR, RuntimeBlockReason.PROVIDER_PROMPT),
    ]

    for excerpt, blocker_reason, block_reason in cases:
        result = _classifier().classify_excerpt(excerpt)
        assert result.delivery_state is DeliveryState.BLOCKED
        assert result.blocker_reason is blocker_reason
        assert result.block_reason is block_reason
        assert result.requires_operator is True


def test_codex_prompt_state_uses_classifier_output() -> None:
    classifier = _classifier()

    assert (
        classifier.prompt_state_for_excerpt("\n› Implement {feature}")
        == "idle_at_default_prompt"
    )
    assert classifier.prompt_state_for_excerpt("\n› Add tests") == "prompt_visible:not_submitted"
    assert classifier.prompt_state_for_excerpt("\n› ") == "ready"


def test_codex_stale_scrollback_is_ignored_when_latest_prompt_is_ready() -> None:
    classifier = _classifier()

    update_result = classifier.classify_excerpt(
        "A new version of Codex is available. Update Codex?\n\n› "
    )
    default_result = classifier.classify_excerpt("\n› Implement {feature}\n\n› ")

    assert update_result.runtime_state is RuntimeState.READY
    assert update_result.blocker_reason is None
    assert default_result.runtime_state is RuntimeState.READY
    assert default_result.blocker_reason is None


def test_codex_classifier_blocks_delivery_for_update_and_unsubmitted_prompts() -> None:
    classifier = _classifier()

    update_interrupt = classifier.blocking_interrupt_for_excerpt(
        "A new version of Codex is available. Update Codex?"
    )
    unsubmitted_interrupt = classifier.blocking_interrupt_for_excerpt("\n› Review PR #287")

    assert update_interrupt is not None
    assert update_interrupt.reason_code.value == "runtime_update_required"
    assert unsubmitted_interrupt is not None
    assert unsubmitted_interrupt.reason_code.value == "prompt_not_submitted"

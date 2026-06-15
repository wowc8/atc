"""Codex runtime integration tests for provider-owned classification."""

from __future__ import annotations

import pytest

from atc.providers.codex import runtime as codex_runtime
from atc.providers.codex.classifier import CodexRuntimeClassifier
from atc.providers.codex.runtime import CodexRuntime
from atc.runtime.models import (
    BlockerReason,
    DeliveryState,
    ReadinessState,
    RoleKind,
    RuntimeSessionHandle,
    RuntimeState,
    RuntimeTransport,
)


def _handle() -> RuntimeSessionHandle:
    return RuntimeSessionHandle(
        session_id="session-1",
        provider_name="codex",
        role=RoleKind.LEADER,
        transport=RuntimeTransport.TMUX,
        tmux_session="atc-test",
        tmux_pane="%1",
    )


@pytest.mark.asyncio
async def test_codex_runtime_inspection_uses_classifier_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_pane_exists(_pane: str) -> bool:
        return True

    async def fake_capture_pane_text(_pane: str, *, lines: int) -> str:
        assert lines == 40
        return "A new version of Codex is available. Update Codex?"

    monkeypatch.setattr(codex_runtime, "pane_exists", fake_pane_exists)
    monkeypatch.setattr(codex_runtime, "capture_pane_text", fake_capture_pane_text)

    inspection = await CodexRuntime(tmux_session="atc-test").inspect_session(_handle())

    assert inspection.readiness is ReadinessState.BLOCKED
    assert inspection.summary == "Codex runtime update prompt visible"
    assert inspection.details["runtime_state"] == RuntimeState.BLOCKED.value
    assert inspection.details["delivery_state"] == DeliveryState.BLOCKED.value
    assert inspection.details["blocker_reason"] == BlockerReason.RUNTIME_UPDATE_REQUIRED.value
    assert inspection.details["provider_observation"] == "codex_update_prompt"
    assert inspection.details["recovery_capabilities"]["can_detect_update_prompt"] is True


@pytest.mark.asyncio
async def test_codex_runtime_missing_pane_uses_neutral_missing_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_pane_exists(_pane: str) -> bool:
        return False

    monkeypatch.setattr(codex_runtime, "pane_exists", fake_pane_exists)

    inspection = await CodexRuntime(tmux_session="atc-test").inspect_session(_handle())

    assert inspection.alive is False
    assert inspection.readiness is ReadinessState.STOPPED
    assert inspection.details["runtime_state"] == RuntimeState.MISSING.value
    assert inspection.details["blocker_reason"] == BlockerReason.PANE_MISSING.value


def test_codex_classifier_marks_managed_workspace_trust_as_safely_resolvable() -> None:
    classifier = CodexRuntimeClassifier()

    classification = classifier.classify_excerpt("Do you trust this folder?")

    assert classification.readiness is ReadinessState.BLOCKED
    assert classification.blocker_reason is BlockerReason.RUNTIME_TRUST_REQUIRED
    assert classification.diagnostics["safe_to_auto_resolve"] is True
    capabilities = classifier.recovery_capabilities().as_dict()
    assert capabilities["can_classify_trust_prompt"] is True
    assert capabilities["can_auto_accept_managed_workspace_trust_prompt"] is True
    assert capabilities["can_distinguish_auth_secret_unknown_permission_prompts"] is True


def test_codex_classifier_keeps_auth_and_permission_unsafe() -> None:
    classifier = CodexRuntimeClassifier()

    auth = classifier.classify_excerpt("Please sign in to continue")
    permission = classifier.classify_excerpt("Allow command: rm -rf /tmp/example?")

    assert auth.blocker_reason is BlockerReason.RUNTIME_AUTH_REQUIRED
    assert auth.diagnostics["safe_to_auto_resolve"] is False
    assert permission.blocker_reason is BlockerReason.RUNTIME_PERMISSION_REQUIRED
    assert permission.diagnostics["safe_to_auto_resolve"] is False

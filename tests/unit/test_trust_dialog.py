"""Unit tests for _accept_trust_dialog detection of Claude Code startup prompts."""

from __future__ import annotations

from unittest.mock import AsyncMock, call, patch

import pytest

from atc.session.ace import _accept_trust_dialog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_pane(output: str) -> patch:
    return patch(
        "atc.session.ace._capture_pane",
        new=AsyncMock(return_value=output),
    )


def _patch_tmux() -> patch:
    return patch("atc.session.ace._tmux_run", new=AsyncMock())


# ---------------------------------------------------------------------------
# Dialog 1: API key selector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_key_dialog_dismissed_with_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    """API key selector dialog is dismissed by sending Enter (OAuth / no-key mode)."""
    monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    outputs = [
        "Detected a custom API key in your environment\nDo you want to use this API key?\n1. Yes\n❯ 2. No (recommended)",
        "Claude Code v2.1 ready",  # second poll sees Claude running
    ]
    with _patch_tmux() as mock_run, patch(
        "atc.session.ace._capture_pane",
        new=AsyncMock(side_effect=outputs),
    ):
        result = await _accept_trust_dialog("pane-1", timeout=5.0)

    assert result is True
    send_keys_calls = [c for c in mock_run.call_args_list if "send-keys" in c.args]
    assert send_keys_calls
    assert "Enter" in send_keys_calls[0].args


@pytest.mark.asyncio
async def test_api_key_dialog_accepted_with_real_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """API key selector dialog selects 'Yes' when a real API key is configured."""
    monkeypatch.setenv("ATC_ANTHROPIC_API_KEY", "sk-ant-api03-realkey")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    outputs = [
        "Detected a custom API key in your environment\nDo you want to use this API key?\n1. Yes\n❯ 2. No (recommended)",
        "Claude Code v2.1 ready",
    ]
    with _patch_tmux() as mock_run, patch(
        "atc.session.ace._capture_pane",
        new=AsyncMock(side_effect=outputs),
    ):
        result = await _accept_trust_dialog("pane-1", timeout=5.0)

    assert result is True
    send_keys_calls = [c for c in mock_run.call_args_list if "send-keys" in c.args]
    key_values = [c.args[-1] for c in send_keys_calls]
    assert "Up" in key_values
    assert "Enter" in key_values
    assert key_values.index("Up") < key_values.index("Enter")


# ---------------------------------------------------------------------------
# Dialog 2: Bypass permissions confirmation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bypass_dialog_accepted_with_down_enter() -> None:
    """Bypass permissions dialog requires Down then Enter to select 'Yes, I accept'."""
    outputs = [
        "By proceeding, you accept all responsibility for actions taken while running in Bypass Permissions mode.\n❯ 1. No, exit\n  2. Yes, I accept",
        "Claude Code v2.1 ready",
    ]
    with _patch_tmux() as mock_run, patch(
        "atc.session.ace._capture_pane",
        new=AsyncMock(side_effect=outputs),
    ):
        result = await _accept_trust_dialog("pane-2", timeout=5.0)

    assert result is True
    send_keys_calls = [c for c in mock_run.call_args_list if "send-keys" in c.args]
    # Must send Down first, then Enter
    keys_sent = [c.args[-1] for c in send_keys_calls]
    assert "Down" in keys_sent
    down_idx = keys_sent.index("Down")
    assert "Enter" in keys_sent[down_idx:]


@pytest.mark.asyncio
async def test_bypass_dialog_detected_by_yes_no_pattern() -> None:
    """Bypass dialog is also detected via 'yes, i accept' + 'no, exit' pattern."""
    outputs = [
        "❯ 1. No, exit\n  2. Yes, I accept\nEnter to confirm",
        "Claude Code v2.1 ready",
    ]
    with _patch_tmux() as mock_run, patch(
        "atc.session.ace._capture_pane",
        new=AsyncMock(side_effect=outputs),
    ):
        result = await _accept_trust_dialog("pane-3", timeout=5.0)

    assert result is True


# ---------------------------------------------------------------------------
# Legacy: trust-folder dialog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_trust_folder_dismissed_with_enter() -> None:
    """Legacy 'trust this folder' dialog is dismissed by sending Enter."""
    outputs = [
        "Do you trust this folder?\nTrust this folder",
        "Claude Code v2.1 ready",
    ]
    with _patch_tmux() as mock_run, patch(
        "atc.session.ace._capture_pane",
        new=AsyncMock(side_effect=outputs),
    ):
        result = await _accept_trust_dialog("pane-4", timeout=5.0)

    assert result is True
    send_keys_calls = [c for c in mock_run.call_args_list if "send-keys" in c.args]
    assert any("Enter" in c.args for c in send_keys_calls)


# ---------------------------------------------------------------------------
# Dialog 3: Security guide / trust-folder (new v2+ variant)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_security_guide_trust_folder_dismissed_with_enter() -> None:
    """New security guide dialog ('Yes, I trust this folder') is dismissed with Enter."""
    outputs = [
        (
            "Claude Code will be able to read, edit, and execute files here.\n"
            "Security guide\n"
            "❯ 1. Yes, I trust this folder\n"
            "  2. No, exit\n"
            "Enter to confirm · Esc to cancel"
        ),
        "Claude Code — ready",  # second poll: TUI running, no dialog triggers
    ]
    with _patch_tmux() as mock_run, patch(
        "atc.session.ace._capture_pane",
        new=AsyncMock(side_effect=outputs),
    ):
        result = await _accept_trust_dialog("pane-sg1", timeout=5.0)

    assert result is True
    send_keys_calls = [c for c in mock_run.call_args_list if "send-keys" in c.args]
    assert any("Enter" in c.args for c in send_keys_calls)


@pytest.mark.asyncio
async def test_security_guide_false_positive_regression() -> None:
    """Regression: pane containing 'Claude Code will be able to read...' must NOT
    early-exit before the dialog is handled — the body text contains 'claude code'
    which previously triggered the 'already running' guard incorrectly."""
    dialog_text = (
        "Claude Code will be able to read, edit, and execute files here.\n"
        "❯ 1. Yes, I trust this folder\n"
        "  2. No, exit\n"
        "Enter to confirm"
    )
    # Both polls return dialog text; function must dismiss it, not bail out early
    outputs = [dialog_text, "Claude Code — ready"]
    with _patch_tmux() as mock_run, patch(
        "atc.session.ace._capture_pane",
        new=AsyncMock(side_effect=outputs),
    ):
        result = await _accept_trust_dialog("pane-sg2", timeout=5.0)

    assert result is True  # dialog WAS handled — not a false "already running"
    send_keys_calls = [c for c in mock_run.call_args_list if "send-keys" in c.args]
    # Enter must have been sent to dismiss the dialog
    assert any("Enter" in c.args for c in send_keys_calls)


# ---------------------------------------------------------------------------
# Already running / no dialog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_false_when_already_running() -> None:
    """Returns False without sending Enter when Claude Code TUI is already up.

    The 'already running' output must NOT contain any dialog trigger strings —
    otherwise the guard would not fire. This tests clean TUI output with no
    dialog keywords present.
    """
    with _patch_tmux() as mock_run, _patch_pane("Welcome to Claude Code v2.1\n> "):
        result = await _accept_trust_dialog("pane-5")

    assert result is False
    send_keys_calls = [c for c in mock_run.call_args_list if "send-keys" in c.args]
    assert not send_keys_calls


@pytest.mark.asyncio
async def test_returns_false_on_timeout() -> None:
    """Returns False when no known dialog appears before timeout."""
    with _patch_tmux(), _patch_pane("some unrelated output"):
        result = await _accept_trust_dialog("pane-6", timeout=0.1)

    assert result is False


# ---------------------------------------------------------------------------
# Same dialog not re-triggered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bypass_dialog_not_re_triggered_on_same_output() -> None:
    """After accepting bypass dialog, same output doesn't trigger it again."""
    bypass_text = "Bypass Permissions mode\n❯ 1. No, exit\n  2. Yes, I accept"
    # Return bypass text twice, then Claude running
    outputs = [bypass_text, bypass_text, "Claude Code v2.1 ready"]
    with _patch_tmux() as mock_run, patch(
        "atc.session.ace._capture_pane",
        new=AsyncMock(side_effect=outputs),
    ):
        result = await _accept_trust_dialog("pane-7", timeout=5.0)

    assert result is True
    # Down+Enter should only be sent once
    send_keys_calls = [c for c in mock_run.call_args_list if "send-keys" in c.args]
    down_count = sum(1 for c in send_keys_calls if "Down" in c.args)
    assert down_count == 1

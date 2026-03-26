"""Regression tests for Tower startup delay fix (PR #155).

Covers:
- Welcome/tips screen strings in _DIALOG_TRIGGERS
- Default timeout is 10.0
- Fast-exit via alternate_on=False + ❯ prompt
- Fast-exit blocked when alternate_on=True
- Dialog trigger strings block the "claude code" text fast-exit
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from atc.session.ace import _DIALOG_TRIGGERS, _accept_trust_dialog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_tmux() -> patch:
    return patch("atc.session.ace._tmux_run", new=AsyncMock())


# ---------------------------------------------------------------------------
# Test 1: Welcome screen strings are in _DIALOG_TRIGGERS
# ---------------------------------------------------------------------------


def test_welcome_screen_strings_in_dialog_triggers() -> None:
    """Welcome/tips screen strings must be in _DIALOG_TRIGGERS to block fast-exit."""
    assert "tips for getting started" in _DIALOG_TRIGGERS
    assert "welcome to claude code" in _DIALOG_TRIGGERS
    assert "welcome back" in _DIALOG_TRIGGERS


# ---------------------------------------------------------------------------
# Test 2: Default timeout is 10.0
# ---------------------------------------------------------------------------


def test_accept_trust_dialog_default_timeout_is_10s() -> None:
    """_accept_trust_dialog default timeout must be 10.0 (not 20.0)."""
    sig = inspect.signature(_accept_trust_dialog)
    timeout_param = sig.parameters["timeout"]
    assert timeout_param.default == 10.0


# ---------------------------------------------------------------------------
# Test 3: Fast-exit fires when alternate_on=False and ❯ in output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_exit_fires_when_alternate_off_and_prompt_visible() -> None:
    """Fast-exit returns early when alternate_on=False and ❯ prompt is visible."""
    with (
        _patch_tmux(),
        patch(
            "atc.session.ace._capture_pane",
            new=AsyncMock(return_value="❯ "),
        ),
        patch(
            "atc.session.ace._get_alternate_on",
            new=AsyncMock(return_value=False),
        ),
    ):
        result = await _accept_trust_dialog("pane-fast-exit", timeout=2.0)

    # Fast-exit fires — no dialogs were dismissed, so result is False
    assert result is False


# ---------------------------------------------------------------------------
# Test 4: Fast-exit does NOT fire when alternate_on=True even with ❯ in output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_exit_blocked_when_alternate_on_true() -> None:
    """Fast-exit must not fire when alternate_on=True, even with ❯ in output."""
    with (
        _patch_tmux(),
        patch(
            "atc.session.ace._capture_pane",
            new=AsyncMock(return_value="❯ "),
        ),
        patch(
            "atc.session.ace._get_alternate_on",
            new=AsyncMock(return_value=True),
        ),
    ):
        # Timeout quickly — the point is that it does NOT fast-exit
        result = await _accept_trust_dialog("pane-no-fast-exit", timeout=0.2)

    # Timed out without triggering fast-exit
    assert result is False


# ---------------------------------------------------------------------------
# Test 5: Dialog trigger string blocks "claude code" text fast-exit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dialog_trigger_blocks_claude_code_fast_exit() -> None:
    """'tips for getting started' in output must prevent the 'claude code' early-exit."""
    # This output contains "claude code" (would normally trigger fast-exit)
    # but also contains a dialog trigger string — so it must NOT exit early.
    # Instead it should wait out the timeout (no dialog keys to press either).
    output = "claude code — tips for getting started\n❯ "
    with (
        _patch_tmux() as mock_run,
        patch(
            "atc.session.ace._capture_pane",
            new=AsyncMock(return_value=output),
        ),
        patch(
            "atc.session.ace._get_alternate_on",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await _accept_trust_dialog("pane-guard", timeout=0.2)

    # Timed out — the fast-exit guard prevented early return
    assert result is False
    # No dialog-dismissal keys should have been sent
    send_keys_calls = [c for c in mock_run.call_args_list if "send-keys" in c.args]
    assert not send_keys_calls

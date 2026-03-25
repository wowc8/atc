"""Unit tests for welcome screen hardening (Issue #131)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from atc.session.ace import _accept_trust_dialog, wait_for_prompt


# ---------------------------------------------------------------------------
# wait_for_prompt tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_prompt_returns_true_on_bare_prompt() -> None:
    """wait_for_prompt returns True when alternate_on=0 and bare '❯' prompt found."""
    with (
        patch("atc.session.ace._get_alternate_on", new=AsyncMock(return_value=False)),
        patch("atc.session.ace._capture_pane", new=AsyncMock(return_value="❯")),
    ):
        result = await wait_for_prompt("pane-1", timeout=2.0)

    assert result is True


@pytest.mark.asyncio
async def test_wait_for_prompt_returns_true_on_gt_prompt() -> None:
    """wait_for_prompt returns True on '> ' bare prompt (alternate style)."""
    with (
        patch("atc.session.ace._get_alternate_on", new=AsyncMock(return_value=False)),
        patch("atc.session.ace._capture_pane", new=AsyncMock(return_value="> ")),
    ):
        result = await wait_for_prompt("pane-2", timeout=2.0)

    assert result is True


@pytest.mark.asyncio
async def test_wait_for_prompt_waits_while_alternate_on() -> None:
    """wait_for_prompt keeps polling while alternate_on == 1."""
    alt_on_values = [True, True, False]
    with (
        patch(
            "atc.session.ace._get_alternate_on",
            new=AsyncMock(side_effect=alt_on_values),
        ),
        patch("atc.session.ace._capture_pane", new=AsyncMock(return_value="❯")),
    ):
        result = await wait_for_prompt("pane-3", timeout=5.0)

    assert result is True


@pytest.mark.asyncio
async def test_wait_for_prompt_returns_false_on_timeout() -> None:
    """wait_for_prompt returns False when prompt never appears."""
    with (
        patch("atc.session.ace._get_alternate_on", new=AsyncMock(return_value=False)),
        patch("atc.session.ace._capture_pane", new=AsyncMock(return_value="some output without prompt")),
    ):
        result = await wait_for_prompt("pane-4", timeout=0.1)

    assert result is False


@pytest.mark.asyncio
async def test_wait_for_prompt_returns_false_on_runtime_error() -> None:
    """wait_for_prompt returns False if pane dies (RuntimeError)."""
    with patch(
        "atc.session.ace._get_alternate_on",
        new=AsyncMock(side_effect=RuntimeError("pane dead")),
    ):
        result = await wait_for_prompt("pane-5", timeout=2.0)

    assert result is False


# ---------------------------------------------------------------------------
# _accept_trust_dialog welcome screen clearing tests
# ---------------------------------------------------------------------------
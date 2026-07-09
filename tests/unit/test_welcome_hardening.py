"""Unit tests for welcome screen hardening (Issue #131)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from atc.providers.claude_code.runtime_helpers import wait_for_prompt

# ---------------------------------------------------------------------------
# wait_for_prompt tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_prompt_returns_true_on_bare_prompt() -> None:
    """wait_for_prompt returns True when alternate_on=0 and bare '❯' prompt found."""
    result = await wait_for_prompt(
        "pane-1",
        get_alternate_on=AsyncMock(return_value=False),
        capture_pane=AsyncMock(return_value="❯"),
        timeout=2.0,
    )
    assert result is True


@pytest.mark.asyncio
async def test_wait_for_prompt_returns_true_on_gt_prompt() -> None:
    """wait_for_prompt returns True on '> ' bare prompt (alternate style)."""
    result = await wait_for_prompt(
        "pane-2",
        get_alternate_on=AsyncMock(return_value=False),
        capture_pane=AsyncMock(return_value="> "),
        timeout=2.0,
    )
    assert result is True


@pytest.mark.asyncio
async def test_wait_for_prompt_waits_while_alternate_on() -> None:
    """wait_for_prompt keeps polling while alternate_on == 1."""
    result = await wait_for_prompt(
        "pane-3",
        get_alternate_on=AsyncMock(side_effect=[True, True, False]),
        capture_pane=AsyncMock(return_value="❯"),
        timeout=5.0,
        poll_interval=0.1,
    )
    assert result is True


@pytest.mark.asyncio
async def test_wait_for_prompt_returns_false_on_timeout() -> None:
    """wait_for_prompt returns False when prompt never appears."""
    result = await wait_for_prompt(
        "pane-4",
        get_alternate_on=AsyncMock(return_value=False),
        capture_pane=AsyncMock(return_value="some output without prompt"),
        timeout=0.1,
        poll_interval=0.05,
    )
    assert result is False


@pytest.mark.asyncio
async def test_wait_for_prompt_returns_false_on_runtime_error() -> None:
    """wait_for_prompt returns False if pane dies (RuntimeError)."""
    result = await wait_for_prompt(
        "pane-5",
        get_alternate_on=AsyncMock(side_effect=RuntimeError("pane dead")),
        capture_pane=AsyncMock(),
        timeout=2.0,
    )
    assert result is False


# ---------------------------------------------------------------------------
# _accept_trust_dialog welcome screen clearing tests
# ---------------------------------------------------------------------------

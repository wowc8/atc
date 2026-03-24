"""Unit tests for startup smoke test (Issue #130)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from atc.core.health import HealthResult, run_startup_smoke_test


@pytest.mark.asyncio
async def test_smoke_test_passes_when_health_ok_appears() -> None:
    """Smoke test returns ok=True when ATC_HEALTH_OK appears in pane output."""
    with (
        patch("atc.core.health._ensure_tmux_session", new=AsyncMock()),
        patch("atc.core.health._spawn_pane", new=AsyncMock(return_value="%99")),
        patch("atc.core.health._tmux_run", new=AsyncMock()),
        patch("atc.core.health._capture_pane", new=AsyncMock(return_value="ATC_HEALTH_OK")),
        patch("atc.core.health._kill_pane", new=AsyncMock()),
    ):
        result = await run_startup_smoke_test()

    assert result.ok is True
    assert "passed" in result.message
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_smoke_test_fails_when_health_ok_never_appears() -> None:
    """Smoke test returns ok=False when ATC_HEALTH_OK never appears (fast timeout)."""
    with (
        patch("atc.core.health._ensure_tmux_session", new=AsyncMock()),
        patch("atc.core.health._spawn_pane", new=AsyncMock(return_value="%99")),
        patch("atc.core.health._tmux_run", new=AsyncMock()),
        patch(
            "atc.core.health._capture_pane",
            new=AsyncMock(return_value="$ bash-4.4"),
        ),
        patch("atc.core.health._kill_pane", new=AsyncMock()),
        patch("atc.core.health.asyncio.sleep", new=AsyncMock()),
        patch("atc.core.health.time") as mock_time,
    ):
        # start=0.0, deadline calc=0.0, while check=11.0 (past deadline), duration calc=11.0
        mock_time.monotonic.side_effect = [0.0, 0.0, 11.0, 11.0]
        result = await run_startup_smoke_test()

    assert result.ok is False
    assert "ATC_HEALTH_OK" in result.message


@pytest.mark.asyncio
async def test_smoke_test_fails_on_spawn_error() -> None:
    """Smoke test returns ok=False when pane spawn raises an exception."""
    with (
        patch("atc.core.health._ensure_tmux_session", new=AsyncMock()),
        patch(
            "atc.core.health._spawn_pane",
            new=AsyncMock(side_effect=RuntimeError("tmux not available")),
        ),
        patch("atc.core.health._kill_pane", new=AsyncMock()),
        patch("atc.core.health.time") as mock_time,
    ):
        mock_time.monotonic.return_value = 0.0
        result = await run_startup_smoke_test()

    assert result.ok is False
    assert "tmux not available" in result.message


@pytest.mark.asyncio
async def test_smoke_test_kills_pane_on_failure() -> None:
    """Pane is killed even when the smoke test fails."""
    kill_mock = AsyncMock()
    with (
        patch("atc.core.health._ensure_tmux_session", new=AsyncMock()),
        patch("atc.core.health._spawn_pane", new=AsyncMock(return_value="%88")),
        patch("atc.core.health._tmux_run", new=AsyncMock()),
        patch("atc.core.health._capture_pane", new=AsyncMock(return_value="no health signal")),
        patch("atc.core.health._kill_pane", kill_mock),
        patch("atc.core.health.asyncio.sleep", new=AsyncMock()),
        patch("atc.core.health.time") as mock_time,
    ):
        # start=0.0, deadline calc=0.0, while check=11.0 (past deadline), duration calc=11.0
        mock_time.monotonic.side_effect = [0.0, 0.0, 11.0, 11.0]
        await run_startup_smoke_test()

    kill_mock.assert_called_once_with("%88")


def test_health_result_dataclass() -> None:
    """HealthResult dataclass has expected fields."""
    r = HealthResult(ok=True, message="passed", duration_ms=42.5)
    assert r.ok is True
    assert r.message == "passed"
    assert r.duration_ms == 42.5

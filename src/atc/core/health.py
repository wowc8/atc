"""Startup smoke test — validates pane spawn + instruction delivery works."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from atc.session.ace import (
    ATC_TMUX_SESSION,
    _capture_pane,
    _ensure_tmux_session,
    _kill_pane,
    _spawn_pane,
    _tmux_run,
)

logger = logging.getLogger(__name__)


@dataclass
class HealthResult:
    """Result of the startup smoke test."""

    ok: bool
    message: str
    duration_ms: float


async def run_startup_smoke_test() -> HealthResult:
    """Spawn a temp tmux pane running bash, send echo ATC_HEALTH_OK, wait for output.

    Returns a HealthResult indicating whether pane spawn and instruction delivery
    work correctly. Kills the pane regardless of outcome.
    """
    start = time.monotonic()
    pane_id: str | None = None

    try:
        await _ensure_tmux_session(ATC_TMUX_SESSION)
        pane_id = await _spawn_pane(ATC_TMUX_SESSION, "bash")

        # Give bash a moment to start
        await asyncio.sleep(0.5)

        # Send the health check command
        await _tmux_run("send-keys", "-t", pane_id, "echo ATC_HEALTH_OK", "Enter")

        # Wait up to 10s for ATC_HEALTH_OK in pane output
        deadline = time.monotonic() + 10.0
        found = False
        while time.monotonic() < deadline:
            try:
                output = await _capture_pane(pane_id)
                if "ATC_HEALTH_OK" in output:
                    found = True
                    break
            except RuntimeError:
                pass
            await asyncio.sleep(0.5)

        duration_ms = (time.monotonic() - start) * 1000
        if found:
            return HealthResult(ok=True, message="smoke test passed", duration_ms=duration_ms)
        return HealthResult(
            ok=False,
            message="ATC_HEALTH_OK not seen in pane output within 10s",
            duration_ms=duration_ms,
        )

    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        return HealthResult(
            ok=False,
            message=f"smoke test error: {exc}",
            duration_ms=duration_ms,
        )
    finally:
        if pane_id is not None:
            try:
                await _kill_pane(pane_id)
            except Exception:
                pass

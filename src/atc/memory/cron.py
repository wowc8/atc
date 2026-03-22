"""Memory consolidation cron — background service that schedules LTM synthesis.

Polls every 15 minutes.  If :func:`MemoryConsolidation.should_run` returns
True, runs a consolidation job as a background task.

On startup, immediately triggers a consolidation if no run has occurred today.

Follows the same start/stop lifecycle pattern as :class:`HeartbeatMonitor`.

Usage::

    cron = MemoryCron(db, event_bus, ws_hub=ws_hub)
    await cron.start()  # in lifespan startup
    await cron.stop()   # in lifespan shutdown
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from atc.memory.consolidation import MemoryConsolidation

if TYPE_CHECKING:
    import aiosqlite

    from atc.api.ws.hub import WsHub
    from atc.core.events import EventBus

logger = logging.getLogger(__name__)

_CHECK_INTERVAL = 15 * 60  # 15 minutes in seconds


class MemoryCron:
    """Background cron that periodically runs LTM memory consolidation."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        event_bus: EventBus,
        *,
        ws_hub: WsHub | None = None,
        check_interval: float = _CHECK_INTERVAL,
    ) -> None:
        self._db = db
        self._event_bus = event_bus
        self._ws_hub = ws_hub
        self._check_interval = check_interval
        self._task: asyncio.Task[None] | None = None
        self._running_job: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background cron loop."""
        if self._task is not None:
            return

        # On startup: trigger immediately if no run today
        try:
            if await MemoryConsolidation.should_run_for_day(self._db):
                logger.info("Memory cron: first run of day — triggering consolidation now")
                self._running_job = asyncio.create_task(self._run_job("startup"))
        except Exception:
            logger.exception("Memory cron: startup check failed")

        self._task = asyncio.create_task(self._cron_loop())
        logger.info(
            "Memory cron started (check_interval=%.0fs)", self._check_interval
        )

    async def stop(self) -> None:
        """Stop the background cron loop."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        if self._running_job is not None and not self._running_job.done():
            self._running_job.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._running_job
            self._running_job = None

        logger.info("Memory cron stopped")

    async def trigger_now(self) -> None:
        """Manually trigger a consolidation run (used by the API endpoint)."""
        if self._running_job is not None and not self._running_job.done():
            logger.info("Memory cron: consolidation already running, skipping manual trigger")
            return
        logger.info("Memory cron: manual consolidation trigger")
        self._running_job = asyncio.create_task(self._run_job("manual"))

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _cron_loop(self) -> None:
        """Poll every check_interval and run consolidation when due."""
        while True:
            await asyncio.sleep(self._check_interval)
            try:
                await self._check_and_run()
            except Exception:
                logger.exception("Memory cron: check iteration failed")

    async def _check_and_run(self) -> None:
        """Check should_run and dispatch a job if needed."""
        # Don't overlap jobs
        if self._running_job is not None and not self._running_job.done():
            logger.debug("Memory cron: consolidation already running, skipping check")
            return

        if not await MemoryConsolidation.should_run(self._db):
            logger.debug("Memory cron: skipping — consolidation not yet due")
            return

        logger.info("Memory cron: consolidation is due — launching job")
        self._running_job = asyncio.create_task(self._run_job("scheduled"))

    async def _run_job(self, trigger: str) -> None:
        """Execute consolidation and log result."""
        logger.info("Memory cron: starting consolidation (trigger=%s)", trigger)
        try:
            result = await MemoryConsolidation.run_consolidation(
                self._db, self._event_bus, self._ws_hub
            )
            logger.info(
                "Memory cron: consolidation done (trigger=%s status=%s written=%d)",
                trigger,
                result.status,
                result.entries_written,
            )
        except Exception:
            logger.exception("Memory cron: consolidation job failed (trigger=%s)", trigger)

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def is_running_job(self) -> bool:
        """Return True if a consolidation job is currently executing."""
        return self._running_job is not None and not self._running_job.done()

    async def last_run_at(self) -> str | None:
        """Return ISO-8601 timestamp of the last finished consolidation run, or None."""
        try:
            cursor = await self._db.execute(
                """SELECT finished_at FROM memory_consolidation_runs
                   WHERE status = 'done' ORDER BY finished_at DESC LIMIT 1"""
            )
            row = await cursor.fetchone()
            return str(row["finished_at"]) if row and row["finished_at"] else None
        except Exception:
            return None

    async def next_run_at(self) -> str | None:
        """Return the estimated ISO-8601 timestamp for the next scheduled run."""
        last = await self.last_run_at()
        if last is None:
            return datetime.now(UTC).isoformat()
        try:
            from datetime import timedelta

            last_dt = datetime.fromisoformat(last)
            next_dt = last_dt + timedelta(hours=3)
            return next_dt.isoformat()
        except ValueError:
            return None

"""Heartbeat monitor — detects stale agent sessions.

The monitor runs a background loop that checks all registered heartbeats
every ``check_interval`` seconds.  If a session has not sent a heartbeat
within ``stale_threshold`` seconds, its health is transitioned to ``stale``.

Health values:
    alive   — heartbeat received within the threshold
    stale   — no heartbeat for > stale_threshold seconds
    stopped — session was cleanly deregistered (or manually marked)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from atc.state import db as db_ops

if TYPE_CHECKING:
    import aiosqlite

    from atc.api.ws.hub import WsHub
    from atc.core.events import EventBus

logger = logging.getLogger(__name__)


class HeartbeatMonitor:
    """Background monitor that marks sessions stale when heartbeats stop."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        event_bus: EventBus,
        *,
        ws_hub: WsHub | None = None,
        check_interval: float = 60.0,
        stale_threshold: float = 120.0,
    ) -> None:
        self._db = db
        self._event_bus = event_bus
        self._ws_hub = ws_hub
        self._check_interval = check_interval
        self._stale_threshold = stale_threshold
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background monitor loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(
            "Heartbeat monitor started (check=%ds, stale=%ds)",
            self._check_interval,
            self._stale_threshold,
        )

    async def stop(self) -> None:
        """Stop the background monitor loop."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            logger.info("Heartbeat monitor stopped")

    async def _monitor_loop(self) -> None:
        """Periodically check heartbeats and mark stale sessions."""
        while True:
            try:
                await self._check_heartbeats()
            except Exception:
                logger.exception("Heartbeat check failed")
            await asyncio.sleep(self._check_interval)

    async def _check_heartbeats(self) -> None:
        """Scan all heartbeat records and transition stale ones."""
        heartbeats = await db_ops.list_heartbeats(self._db)
        now = datetime.now(UTC)

        for hb in heartbeats:
            if hb.health == "stopped":
                continue

            last = datetime.fromisoformat(hb.last_heartbeat_at)
            age = (now - last).total_seconds()

            if age > self._stale_threshold and hb.health != "stale":
                await db_ops.update_heartbeat_health(
                    self._db, hb.session_id, "stale"
                )
                logger.warning(
                    "Session %s marked stale (no heartbeat for %.0fs)",
                    hb.session_id,
                    age,
                )
                await self._event_bus.publish(
                    "session_heartbeat_stale",
                    {"session_id": hb.session_id, "age_seconds": age},
                )
                if self._ws_hub:
                    await self._ws_hub.broadcast(
                        "heartbeat",
                        {
                            "session_id": hb.session_id,
                            "health": "stale",
                            "last_heartbeat_at": hb.last_heartbeat_at,
                        },
                    )
            elif age <= self._stale_threshold and hb.health == "stale":
                # Recovered — mark alive again
                await db_ops.update_heartbeat_health(
                    self._db, hb.session_id, "alive"
                )
                logger.info("Session %s heartbeat recovered", hb.session_id)
                if self._ws_hub:
                    await self._ws_hub.broadcast(
                        "heartbeat",
                        {
                            "session_id": hb.session_id,
                            "health": "alive",
                            "last_heartbeat_at": hb.last_heartbeat_at,
                        },
                    )

    async def handle_heartbeat(self, session_id: str) -> bool:
        """Process an incoming heartbeat from a session.

        Auto-registers the session if not already tracked.
        Returns True if recorded successfully.
        """
        recorded = await db_ops.record_heartbeat(self._db, session_id)
        if not recorded:
            try:
                await db_ops.register_heartbeat(self._db, session_id)
                recorded = True
            except Exception:
                # Session ID not in sessions table (stale reference after cleardb).
                # Ignore silently — heartbeat for a non-existent session is harmless.
                return False

        if self._ws_hub:
            now = datetime.now(UTC).isoformat()
            await self._ws_hub.broadcast(
                "heartbeat",
                {
                    "session_id": session_id,
                    "health": "alive",
                    "last_heartbeat_at": now,
                },
            )
        return recorded

    async def register(self, session_id: str) -> None:
        """Register a session for heartbeat tracking."""
        await db_ops.register_heartbeat(self._db, session_id)
        logger.info("Session %s registered for heartbeat", session_id)

    async def deregister(self, session_id: str) -> None:
        """Cleanly deregister a session (expected shutdown)."""
        await db_ops.deregister_heartbeat(self._db, session_id)
        logger.info("Session %s deregistered from heartbeat", session_id)
        if self._ws_hub:
            await self._ws_hub.broadcast(
                "heartbeat",
                {"session_id": session_id, "health": "stopped"},
            )

"""State leader — queued DB write handler for session events.

Subscribes to event bus events and persists state changes to the database.
This decouples the fast event publishing path from slower DB writes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite

    from atc.core.events import EventBus

from atc.state.db import update_session_status

logger = logging.getLogger(__name__)


class StateLeader:
    """Processes session state change events and writes them to the DB.

    Uses an asyncio.Queue to serialize writes and avoid DB contention.
    """

    def __init__(self, db: aiosqlite.Connection, event_bus: EventBus) -> None:
        self._db = db
        self._event_bus = event_bus
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start listening for events and processing the write queue."""
        self._event_bus.subscribe("session_status_changed", self._on_status_changed)
        self._task = asyncio.create_task(self._process_queue())
        logger.info("StateLeader started")

    async def stop(self) -> None:
        """Stop the queue processor."""
        self._event_bus.unsubscribe("session_status_changed", self._on_status_changed)
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        # Drain remaining items
        while not self._queue.empty():
            event = self._queue.get_nowait()
            await self._write_status(event)
        logger.info("StateLeader stopped")

    async def _on_status_changed(self, data: dict[str, Any]) -> None:
        """Event handler — enqueue the status change for DB write."""
        await self._queue.put(data)

    async def _process_queue(self) -> None:
        """Background loop that writes queued status changes to the DB."""
        while True:
            event = await self._queue.get()
            await self._write_status(event)
            self._queue.task_done()

    async def _write_status(self, event: dict[str, Any]) -> None:
        """Persist a single status change."""
        session_id = event.get("session_id")
        new_status = event.get("new_status")
        if not session_id or not new_status:
            logger.warning("Invalid status event: %s", event)
            return
        try:
            await update_session_status(self._db, session_id, new_status)
        except Exception:
            logger.exception("Failed to write status for session %s", session_id)

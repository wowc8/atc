"""Per-session monitor loop — detects status changes and publishes events.

Each monitored session gets an asyncio task that:
1. Receives PTY output chunks from the PtyStreamReader
2. Feeds them through the OutputParser
3. Detects state transitions (idle → working, working → waiting, etc.)
4. Publishes status-change events on the event bus
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from atc.terminal.output_parser import OutputParser, TuiState

if TYPE_CHECKING:
    from atc.core.events import EventBus
    from atc.terminal.pty_stream import PtyStreamPool

logger = logging.getLogger(__name__)

# Map TUI states to session status strings used in DB / events
_STATE_TO_STATUS: dict[TuiState, str] = {
    TuiState.SHELL_PROMPT: "idle",
    TuiState.CLAUDE_IDLE: "idle",
    TuiState.CLAUDE_WORKING: "working",
    TuiState.CLAUDE_WAITING: "waiting",
    TuiState.ALTERNATE_SCREEN: "working",  # e.g. vim inside Claude
}


class SessionMonitor:
    """Monitors a single session's PTY output for status changes."""

    def __init__(
        self,
        session_id: str,
        event_bus: EventBus,
    ) -> None:
        self._session_id = session_id
        self._event_bus = event_bus
        self._parser = OutputParser()
        self._current_status: str = "idle"
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1024)
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def current_status(self) -> str:
        return self._current_status

    @property
    def alternate_on(self) -> bool:
        return self._parser.alternate_on

    @property
    def running(self) -> bool:
        return self._running

    def enqueue(self, session_id: str, data: bytes) -> None:
        """Callback for PtyStreamReader — enqueue a chunk for processing."""
        if session_id != self._session_id:
            return
        try:
            self._queue.put_nowait(data)
        except asyncio.QueueFull:
            logger.warning("Monitor queue full for %s, dropping chunk", self._session_id)

    async def start(self) -> None:
        """Start the monitor loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._monitor_loop(), name=f"monitor-{self._session_id}"
        )
        logger.info("SessionMonitor started for %s", self._session_id)

    async def stop(self) -> None:
        """Stop the monitor loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("SessionMonitor stopped for %s", self._session_id)

    async def _monitor_loop(self) -> None:
        """Process queued PTY chunks and detect status transitions."""
        while self._running:
            try:
                chunk = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise

            try:
                result = self._parser.feed(chunk)
                new_status = _STATE_TO_STATUS.get(result.state)

                if new_status and new_status != self._current_status:
                    old_status = self._current_status
                    self._current_status = new_status
                    await self._event_bus.publish("session_status_changed", {
                        "session_id": self._session_id,
                        "old_status": old_status,
                        "new_status": new_status,
                        "tui_state": result.state.value,
                        "alternate_on": result.alternate_on,
                    })

                # Publish cost events when detected
                if result.cost_dollars is not None:
                    await self._event_bus.publish("session_cost_update", {
                        "session_id": self._session_id,
                        "cost_dollars": result.cost_dollars,
                        "tokens_in": result.tokens_in,
                        "tokens_out": result.tokens_out,
                    })

                # Publish error events when detected
                if result.error_text:
                    await self._event_bus.publish("session_error", {
                        "session_id": self._session_id,
                        "error_text": result.error_text,
                    })

            except Exception:
                logger.exception("Monitor parse error for %s", self._session_id)


class MonitorPool:
    """Manages SessionMonitor instances for all active sessions.

    Coordinates with PtyStreamPool to register data callbacks
    and manages the lifecycle of per-session monitors.
    """

    def __init__(
        self,
        event_bus: EventBus,
        pty_pool: PtyStreamPool,
    ) -> None:
        self._event_bus = event_bus
        self._pty_pool = pty_pool
        self._monitors: dict[str, SessionMonitor] = {}

    @property
    def session_ids(self) -> list[str]:
        return list(self._monitors.keys())

    def get_monitor(self, session_id: str) -> SessionMonitor | None:
        return self._monitors.get(session_id)

    async def add_session(self, session_id: str) -> SessionMonitor:
        """Create and start a monitor for the given session.

        The monitor is wired to the session's PtyStreamReader as a data callback.
        """
        if session_id in self._monitors:
            await self.remove_session(session_id)

        monitor = SessionMonitor(
            session_id=session_id,
            event_bus=self._event_bus,
        )

        # Wire up the PTY reader callback
        reader = self._pty_pool.get_reader(session_id)
        if reader:
            reader.on_data(monitor.enqueue)

        await monitor.start()
        self._monitors[session_id] = monitor
        return monitor

    async def remove_session(self, session_id: str) -> None:
        """Stop and remove the monitor for the given session."""
        monitor = self._monitors.pop(session_id, None)
        if monitor:
            await monitor.stop()

    async def stop_all(self) -> None:
        """Stop all monitors."""
        for monitor in list(self._monitors.values()):
            await monitor.stop()
        self._monitors.clear()

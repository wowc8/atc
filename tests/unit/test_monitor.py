"""Unit tests for SessionMonitor and MonitorPool."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from atc.core.events import EventBus
from atc.terminal.monitor import MonitorPool, SessionMonitor
from atc.terminal.pty_stream import PtyStreamPool, PtyStreamReader


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


# ---------------------------------------------------------------------------
# SessionMonitor
# ---------------------------------------------------------------------------

class TestSessionMonitor:
    @pytest.fixture
    def monitor(self, event_bus: EventBus) -> SessionMonitor:
        return SessionMonitor(session_id="sess-1", event_bus=event_bus)

    def test_properties(self, monitor: SessionMonitor) -> None:
        assert monitor.session_id == "sess-1"
        assert monitor.current_status == "idle"
        assert monitor.alternate_on is False
        assert monitor.running is False

    async def test_start_stop(self, monitor: SessionMonitor) -> None:
        await monitor.start()
        assert monitor.running is True
        await monitor.stop()
        assert monitor.running is False

    async def test_start_idempotent(self, monitor: SessionMonitor) -> None:
        await monitor.start()
        await monitor.start()  # no-op
        assert monitor.running is True
        await monitor.stop()

    def test_enqueue(self, monitor: SessionMonitor) -> None:
        monitor.enqueue("sess-1", b"hello")
        assert monitor._queue.qsize() == 1

    def test_enqueue_wrong_session(self, monitor: SessionMonitor) -> None:
        monitor.enqueue("other-session", b"hello")
        assert monitor._queue.qsize() == 0

    async def test_detects_working_status(
        self, monitor: SessionMonitor, event_bus: EventBus
    ) -> None:
        events: list[dict] = []

        async def handler(data: dict) -> None:
            events.append(data)

        event_bus.subscribe("session_status_changed", handler)

        await monitor.start()
        monitor.enqueue("sess-1", b"\xe2\xa0\x8b Thinking...")  # ⠋ spinner
        await asyncio.sleep(0.2)
        await monitor.stop()

        assert len(events) >= 1
        assert events[0]["session_id"] == "sess-1"
        assert events[0]["old_status"] == "idle"
        assert events[0]["new_status"] == "working"

    async def test_detects_waiting_status(
        self, monitor: SessionMonitor, event_bus: EventBus
    ) -> None:
        events: list[dict] = []

        async def handler(data: dict) -> None:
            events.append(data)

        event_bus.subscribe("session_status_changed", handler)

        await monitor.start()
        # First make it "working"
        monitor.enqueue("sess-1", b"Working on task...")
        await asyncio.sleep(0.1)
        # Then transition to "waiting"
        monitor.enqueue("sess-1", b"Do you want to proceed?")
        await asyncio.sleep(0.2)
        await monitor.stop()

        statuses = [e["new_status"] for e in events]
        assert "working" in statuses
        assert "waiting" in statuses

    async def test_publishes_cost_event(
        self, monitor: SessionMonitor, event_bus: EventBus
    ) -> None:
        costs: list[dict] = []

        async def handler(data: dict) -> None:
            costs.append(data)

        event_bus.subscribe("session_cost_update", handler)

        await monitor.start()
        monitor.enqueue("sess-1", b"Cost: $0.15 | Tokens: 2.0k in, 1.5k out")
        await asyncio.sleep(0.2)
        await monitor.stop()

        assert len(costs) >= 1
        assert costs[0]["cost_dollars"] == 0.15
        assert costs[0]["session_id"] == "sess-1"

    async def test_publishes_error_event(
        self, monitor: SessionMonitor, event_bus: EventBus
    ) -> None:
        errors: list[dict] = []

        async def handler(data: dict) -> None:
            errors.append(data)

        event_bus.subscribe("session_error", handler)

        await monitor.start()
        monitor.enqueue("sess-1", b"Error: connection refused")
        await asyncio.sleep(0.2)
        await monitor.stop()

        assert len(errors) >= 1
        assert "connection refused" in errors[0]["error_text"]

    async def test_no_duplicate_status_events(
        self, monitor: SessionMonitor, event_bus: EventBus
    ) -> None:
        """Same status should not trigger duplicate events."""
        events: list[dict] = []

        async def handler(data: dict) -> None:
            events.append(data)

        event_bus.subscribe("session_status_changed", handler)

        await monitor.start()
        monitor.enqueue("sess-1", b"Working on task...")
        await asyncio.sleep(0.1)
        monitor.enqueue("sess-1", b"Still working...")  # same state
        await asyncio.sleep(0.2)
        await monitor.stop()

        # Should only get one transition to "working"
        working_events = [e for e in events if e["new_status"] == "working"]
        assert len(working_events) == 1

    async def test_queue_full_drops(self, monitor: SessionMonitor) -> None:
        """When queue is full, new chunks should be dropped without error."""
        for _ in range(1100):
            monitor.enqueue("sess-1", b"x")
        # Queue maxsize is 1024, so some were dropped — no error raised


# ---------------------------------------------------------------------------
# MonitorPool
# ---------------------------------------------------------------------------

class TestMonitorPool:
    @pytest.fixture
    def pty_pool(self, event_bus: EventBus, tmp_path: object) -> MagicMock:
        pool = MagicMock(spec=PtyStreamPool)
        pool.get_reader.return_value = MagicMock(spec=PtyStreamReader)
        return pool

    @pytest.fixture
    def monitor_pool(
        self, event_bus: EventBus, pty_pool: MagicMock
    ) -> MonitorPool:
        return MonitorPool(event_bus=event_bus, pty_pool=pty_pool)

    async def test_add_session(
        self, monitor_pool: MonitorPool, pty_pool: MagicMock
    ) -> None:
        monitor = await monitor_pool.add_session("sess-1")
        assert monitor.session_id == "sess-1"
        assert monitor.running is True
        assert "sess-1" in monitor_pool.session_ids
        await monitor_pool.stop_all()

    async def test_remove_session(self, monitor_pool: MonitorPool) -> None:
        await monitor_pool.add_session("sess-1")
        await monitor_pool.remove_session("sess-1")
        assert "sess-1" not in monitor_pool.session_ids

    async def test_get_monitor(self, monitor_pool: MonitorPool) -> None:
        await monitor_pool.add_session("sess-1")
        m = monitor_pool.get_monitor("sess-1")
        assert m is not None
        assert m.session_id == "sess-1"
        await monitor_pool.stop_all()

    async def test_get_monitor_nonexistent(self, monitor_pool: MonitorPool) -> None:
        assert monitor_pool.get_monitor("nope") is None

    async def test_add_session_replaces_existing(
        self, monitor_pool: MonitorPool
    ) -> None:
        m1 = await monitor_pool.add_session("sess-1")
        m2 = await monitor_pool.add_session("sess-1")
        assert m1 is not m2
        assert m1.running is False
        assert m2.running is True
        await monitor_pool.stop_all()

    async def test_stop_all(self, monitor_pool: MonitorPool) -> None:
        await monitor_pool.add_session("sess-1")
        await monitor_pool.add_session("sess-2")
        await monitor_pool.stop_all()
        assert monitor_pool.session_ids == []

    async def test_wires_pty_callback(
        self, monitor_pool: MonitorPool, pty_pool: MagicMock
    ) -> None:
        reader = pty_pool.get_reader.return_value
        await monitor_pool.add_session("sess-1")
        reader.on_data.assert_called_once()
        await monitor_pool.stop_all()

    async def test_handles_no_reader(
        self, monitor_pool: MonitorPool, pty_pool: MagicMock
    ) -> None:
        """If PTY reader doesn't exist yet, monitor should still start."""
        pty_pool.get_reader.return_value = None
        monitor = await monitor_pool.add_session("sess-1")
        assert monitor.running is True
        await monitor_pool.stop_all()

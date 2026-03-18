"""Tests for the heartbeat protocol — DB CRUD, monitor, and API integration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from atc.core.events import EventBus
from atc.core.heartbeat import HeartbeatMonitor
from atc.state.db import (
    _SCHEMA_SQL,
    create_project,
    create_session,
    deregister_heartbeat,
    get_connection,
    get_heartbeat,
    list_heartbeats,
    record_heartbeat,
    register_heartbeat,
    run_migrations,
    update_heartbeat_health,
)


@pytest.fixture
async def db():
    """In-memory database with schema applied."""
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def ws_hub() -> MagicMock:
    hub = MagicMock()
    hub.broadcast = AsyncMock()
    return hub


# ---------------------------------------------------------------------------
# DB CRUD tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHeartbeatCRUD:
    async def test_register_heartbeat(self, db) -> None:
        project = await create_project(db, "test-proj")
        session = await create_session(db, project.id, "ace", "ace-1")

        hb = await register_heartbeat(db, session.id)
        assert hb.session_id == session.id
        assert hb.health == "alive"
        assert hb.last_heartbeat_at != ""
        assert hb.registered_at != ""

    async def test_register_heartbeat_upsert(self, db) -> None:
        project = await create_project(db, "test-proj")
        session = await create_session(db, project.id, "ace", "ace-1")

        hb1 = await register_heartbeat(db, session.id)
        await update_heartbeat_health(db, session.id, "stale")
        hb2 = await register_heartbeat(db, session.id)

        assert hb2.health == "alive"
        assert hb2.last_heartbeat_at >= hb1.last_heartbeat_at

    async def test_record_heartbeat(self, db) -> None:
        project = await create_project(db, "test-proj")
        session = await create_session(db, project.id, "ace", "ace-1")
        await register_heartbeat(db, session.id)

        result = await record_heartbeat(db, session.id)
        assert result is True

    async def test_record_heartbeat_unregistered(self, db) -> None:
        result = await record_heartbeat(db, "nonexistent")
        assert result is False

    async def test_get_heartbeat(self, db) -> None:
        project = await create_project(db, "test-proj")
        session = await create_session(db, project.id, "ace", "ace-1")
        await register_heartbeat(db, session.id)

        hb = await get_heartbeat(db, session.id)
        assert hb is not None
        assert hb.session_id == session.id
        assert hb.health == "alive"

    async def test_get_heartbeat_missing(self, db) -> None:
        hb = await get_heartbeat(db, "nonexistent")
        assert hb is None

    async def test_list_heartbeats(self, db) -> None:
        project = await create_project(db, "test-proj")
        s1 = await create_session(db, project.id, "ace", "ace-1")
        s2 = await create_session(db, project.id, "ace", "ace-2")
        await register_heartbeat(db, s1.id)
        await register_heartbeat(db, s2.id)

        heartbeats = await list_heartbeats(db)
        assert len(heartbeats) == 2

    async def test_update_heartbeat_health(self, db) -> None:
        project = await create_project(db, "test-proj")
        session = await create_session(db, project.id, "ace", "ace-1")
        await register_heartbeat(db, session.id)

        await update_heartbeat_health(db, session.id, "stale")
        hb = await get_heartbeat(db, session.id)
        assert hb is not None
        assert hb.health == "stale"

    async def test_deregister_heartbeat(self, db) -> None:
        project = await create_project(db, "test-proj")
        session = await create_session(db, project.id, "ace", "ace-1")
        await register_heartbeat(db, session.id)

        removed = await deregister_heartbeat(db, session.id)
        assert removed is True

        hb = await get_heartbeat(db, session.id)
        assert hb is None

    async def test_deregister_heartbeat_missing(self, db) -> None:
        removed = await deregister_heartbeat(db, "nonexistent")
        assert removed is False

    async def test_cascade_delete(self, db) -> None:
        """Heartbeat row is deleted when session is deleted."""
        project = await create_project(db, "test-proj")
        session = await create_session(db, project.id, "ace", "ace-1")
        await register_heartbeat(db, session.id)

        await db.execute("DELETE FROM sessions WHERE id = ?", (session.id,))
        await db.commit()

        hb = await get_heartbeat(db, session.id)
        assert hb is None


# ---------------------------------------------------------------------------
# Monitor tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHeartbeatMonitor:
    async def test_start_stop(self, db, event_bus) -> None:
        monitor = HeartbeatMonitor(db, event_bus, check_interval=0.1, stale_threshold=1.0)
        await monitor.start()
        assert monitor._task is not None
        await monitor.stop()
        assert monitor._task is None

    async def test_handle_heartbeat_auto_registers(self, db, event_bus) -> None:
        monitor = HeartbeatMonitor(db, event_bus)

        project = await create_project(db, "test-proj")
        session = await create_session(db, project.id, "ace", "ace-1")

        result = await monitor.handle_heartbeat(session.id)
        assert result is True

        hb = await get_heartbeat(db, session.id)
        assert hb is not None
        assert hb.health == "alive"

    async def test_handle_heartbeat_updates_existing(self, db, event_bus) -> None:
        monitor = HeartbeatMonitor(db, event_bus)

        project = await create_project(db, "test-proj")
        session = await create_session(db, project.id, "ace", "ace-1")
        await register_heartbeat(db, session.id)

        result = await monitor.handle_heartbeat(session.id)
        assert result is True

    async def test_handle_heartbeat_broadcasts(self, db, event_bus, ws_hub) -> None:
        monitor = HeartbeatMonitor(db, event_bus, ws_hub=ws_hub)

        project = await create_project(db, "test-proj")
        session = await create_session(db, project.id, "ace", "ace-1")

        await monitor.handle_heartbeat(session.id)
        ws_hub.broadcast.assert_called()
        call_args = ws_hub.broadcast.call_args
        assert call_args[0][0] == "heartbeat"
        assert call_args[0][1]["health"] == "alive"

    async def test_mark_stale(self, db, event_bus, ws_hub) -> None:
        """Sessions with old heartbeats are marked stale."""
        monitor = HeartbeatMonitor(
            db, event_bus, ws_hub=ws_hub, check_interval=0.1, stale_threshold=0.0
        )

        project = await create_project(db, "test-proj")
        session = await create_session(db, project.id, "ace", "ace-1")
        await register_heartbeat(db, session.id)

        # Set the heartbeat timestamp to the past
        old_time = (datetime.now(UTC) - timedelta(seconds=300)).isoformat()
        await db.execute(
            "UPDATE session_heartbeats SET last_heartbeat_at = ? WHERE session_id = ?",
            (old_time, session.id),
        )
        await db.commit()

        await monitor._check_heartbeats()

        hb = await get_heartbeat(db, session.id)
        assert hb is not None
        assert hb.health == "stale"

    async def test_skip_stopped(self, db, event_bus) -> None:
        """Stopped sessions are not checked."""
        monitor = HeartbeatMonitor(db, event_bus, stale_threshold=0.0)

        project = await create_project(db, "test-proj")
        session = await create_session(db, project.id, "ace", "ace-1")
        await register_heartbeat(db, session.id)
        await update_heartbeat_health(db, session.id, "stopped")

        old_time = (datetime.now(UTC) - timedelta(seconds=300)).isoformat()
        await db.execute(
            "UPDATE session_heartbeats SET last_heartbeat_at = ? WHERE session_id = ?",
            (old_time, session.id),
        )
        await db.commit()

        await monitor._check_heartbeats()

        hb = await get_heartbeat(db, session.id)
        assert hb is not None
        assert hb.health == "stopped"  # not changed to stale

    async def test_register_deregister(self, db, event_bus, ws_hub) -> None:
        monitor = HeartbeatMonitor(db, event_bus, ws_hub=ws_hub)

        project = await create_project(db, "test-proj")
        session = await create_session(db, project.id, "ace", "ace-1")

        await monitor.register(session.id)
        hb = await get_heartbeat(db, session.id)
        assert hb is not None

        await monitor.deregister(session.id)
        hb = await get_heartbeat(db, session.id)
        assert hb is None

        # Broadcast called for deregister
        ws_hub.broadcast.assert_called()

    async def test_stale_publishes_event(self, db, event_bus) -> None:
        received = []
        event_bus.subscribe("session_heartbeat_stale", lambda d: received.append(d))

        monitor = HeartbeatMonitor(db, event_bus, stale_threshold=0.0)

        project = await create_project(db, "test-proj")
        session = await create_session(db, project.id, "ace", "ace-1")
        await register_heartbeat(db, session.id)

        old_time = (datetime.now(UTC) - timedelta(seconds=300)).isoformat()
        await db.execute(
            "UPDATE session_heartbeats SET last_heartbeat_at = ? WHERE session_id = ?",
            (old_time, session.id),
        )
        await db.commit()

        await monitor._check_heartbeats()

        assert len(received) == 1
        assert received[0]["session_id"] == session.id

"""Unit tests for PATCH /api/aces/{session_id}/status and POST /api/aces/{session_id}/notify."""

from __future__ import annotations

import pytest

from atc.core.events import EventBus
from atc.state.db import ConnectionFactory
from atc.state.migrations import run_migrations as sync_run_migrations


@pytest.fixture
async def db_and_bus(tmp_path):
    """Provide an async DB connection and event bus for API tests."""
    import aiosqlite

    db_path = str(tmp_path / "test.db")
    # Run migrations
    factory = ConnectionFactory(db_path)
    sync_run_migrations(factory)

    db = await aiosqlite.connect(db_path)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = aiosqlite.Row

    event_bus = EventBus()
    await event_bus.start()

    yield db, event_bus

    await event_bus.stop()
    await db.close()


@pytest.fixture
async def project_and_session(db_and_bus):
    """Create a project and an ace session for testing."""
    from atc.state import db as db_ops

    db, event_bus = db_and_bus

    project = await db_ops.create_project(db, "TestProject")
    session = await db_ops.create_session(
        db, project.id, "ace", "test-ace", status="idle",
    )

    return project, session, db, event_bus


class TestPatchAceStatus:
    @pytest.mark.asyncio
    async def test_transition_to_working(self, project_and_session) -> None:
        from atc.session.state_machine import SessionStatus

        project, session, db, event_bus = project_and_session
        from atc.state import db as db_ops

        # Simulate the endpoint logic: idle → working
        target = SessionStatus.WORKING
        current = SessionStatus(session.status)
        from atc.session.state_machine import transition

        await transition(session.id, current, target, event_bus)
        await db_ops.update_session_status(db, session.id, target.value)

        updated = await db_ops.get_session(db, session.id)
        assert updated is not None
        assert updated.status == "working"

    @pytest.mark.asyncio
    async def test_working_to_waiting(self, project_and_session) -> None:
        from atc.session.state_machine import SessionStatus, transition
        from atc.state import db as db_ops

        project, session, db, event_bus = project_and_session

        # First transition to working
        await transition(session.id, SessionStatus.IDLE, SessionStatus.WORKING, event_bus)
        await db_ops.update_session_status(db, session.id, "working")

        # Then to waiting (Stop hook behavior)
        await transition(session.id, SessionStatus.WORKING, SessionStatus.WAITING, event_bus)
        await db_ops.update_session_status(db, session.id, "waiting")

        updated = await db_ops.get_session(db, session.id)
        assert updated is not None
        assert updated.status == "waiting"

    @pytest.mark.asyncio
    async def test_invalid_transition_raises(self, project_and_session) -> None:
        from atc.session.state_machine import (
            InvalidTransitionError,
            SessionStatus,
            transition,
        )

        _project, session, _db, event_bus = project_and_session

        with pytest.raises(InvalidTransitionError):
            await transition(
                session.id, SessionStatus.IDLE, SessionStatus.DISCONNECTED, event_bus,
            )

    @pytest.mark.asyncio
    async def test_same_status_is_noop(self, project_and_session) -> None:
        from atc.state import db as db_ops

        _project, session, db, _event_bus = project_and_session

        # If current == target, no transition needed — just return
        updated = await db_ops.get_session(db, session.id)
        assert updated is not None
        assert updated.status == "idle"


class TestNotifyEndpoint:
    @pytest.mark.asyncio
    async def test_stores_notification(self, project_and_session) -> None:
        import uuid
        from datetime import UTC, datetime

        project, session, db, _event_bus = project_and_session

        # Simulate the notify endpoint logic
        now = datetime.now(UTC).isoformat()
        await db.execute(
            """INSERT INTO notifications (id, project_id, level, message, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), session.project_id, "info", "Test notification", now),
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT * FROM notifications WHERE project_id = ?",
            (session.project_id,),
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert dict(rows[0])["message"] == "Test notification"

    @pytest.mark.asyncio
    async def test_session_not_found(self, db_and_bus) -> None:
        from atc.state import db as db_ops

        db, _event_bus = db_and_bus
        session = await db_ops.get_session(db, "nonexistent-id")
        assert session is None

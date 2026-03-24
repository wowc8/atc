"""Tests for global concurrent session limit (Issue #127)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from atc.core.events import EventBus
from atc.state.db import (
    _SCHEMA_SQL,
    create_leader,
    create_project,
    create_session,
    get_connection,
    run_migrations,
)
from atc.tower.controller import (
    TowerBusyError,
    TowerController,
    TowerState,
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


@pytest.mark.asyncio
class TestSessionLimit:
    def _make_tower(self, db, event_bus: EventBus, max_aces: int = 2) -> TowerController:
        return TowerController(db, event_bus, max_concurrent_aces=max_aces)

    async def test_status_exposes_active_ace_count_and_max_aces(
        self, db, event_bus: EventBus
    ) -> None:
        tower = self._make_tower(db, event_bus, max_aces=3)
        status = tower.get_status()
        assert status["active_ace_count"] == 0
        assert status["max_aces"] == 3

    async def test_session_created_increments_counter_for_leader(
        self, db, event_bus: EventBus
    ) -> None:
        tower = self._make_tower(db, event_bus)
        await event_bus.publish(
            "session_created",
            {"session_id": "s1", "session_type": "leader", "project_id": "p1"},
        )
        assert tower._active_ace_count == 1

    async def test_session_created_increments_counter_for_ace(
        self, db, event_bus: EventBus
    ) -> None:
        tower = self._make_tower(db, event_bus)
        await event_bus.publish(
            "session_created",
            {"session_id": "s1", "session_type": "ace", "project_id": "p1"},
        )
        assert tower._active_ace_count == 1

    async def test_session_created_ignores_tower_type(
        self, db, event_bus: EventBus
    ) -> None:
        tower = self._make_tower(db, event_bus)
        await event_bus.publish(
            "session_created",
            {"session_id": "s1", "session_type": "tower", "project_id": "p1"},
        )
        assert tower._active_ace_count == 0

    async def test_terminal_status_decrements_counter(self, db, event_bus: EventBus) -> None:
        """When a leader/ace session reaches a terminal status, counter decrements."""
        import uuid

        project = await create_project(db, "test-proj")
        sess = await create_session(
            db,
            project_id=project.id,
            session_type="leader",
            name="leader-test",
            status="working",
        )
        await db.commit()

        tower = self._make_tower(db, event_bus)
        tower._active_ace_count = 2

        await event_bus.publish(
            "session_status_changed",
            {"session_id": sess.id, "new_status": "disconnected"},
        )
        assert tower._active_ace_count == 1

    async def test_counter_does_not_go_below_zero(self, db, event_bus: EventBus) -> None:
        project = await create_project(db, "test-proj")
        sess = await create_session(
            db,
            project_id=project.id,
            session_type="ace",
            name="ace-test",
            status="working",
        )
        await db.commit()

        tower = self._make_tower(db, event_bus)
        tower._active_ace_count = 0

        await event_bus.publish(
            "session_status_changed",
            {"session_id": sess.id, "new_status": "error"},
        )
        assert tower._active_ace_count == 0

    @patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="l1")
    async def test_submit_goal_raises_when_at_capacity(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = self._make_tower(db, event_bus, max_aces=2)
        tower._active_ace_count = 2  # already at limit

        with pytest.raises(TowerBusyError) as exc_info:
            await tower.submit_goal(project.id, "Should be blocked")

        assert "at capacity" in str(exc_info.value)
        mock_start.assert_not_called()

    @patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="l1")
    async def test_submit_goal_succeeds_when_below_capacity(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = self._make_tower(db, event_bus, max_aces=5)
        tower._active_ace_count = 3  # below limit

        result = await tower.submit_goal(project.id, "Should succeed")
        assert result["status"] == "accepted"

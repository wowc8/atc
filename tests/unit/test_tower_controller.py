"""Tests for TowerController lifecycle and state transitions."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from atc.core.events import EventBus
from atc.state.db import (
    _SCHEMA_SQL,
    create_leader,
    create_project,
    get_connection,
    run_migrations,
)
from atc.tower.controller import (
    InvalidTowerTransitionError,
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


@pytest.fixture
def tower(db, event_bus) -> TowerController:
    return TowerController(db, event_bus)


@pytest.mark.asyncio
class TestTowerState:
    async def test_initial_state_is_idle(self, tower: TowerController) -> None:
        assert tower.state == TowerState.IDLE

    async def test_get_status(self, tower: TowerController) -> None:
        status = tower.get_status()
        assert status["state"] == "idle"
        assert status["current_goal"] is None
        assert status["current_project_id"] is None
        assert status["current_session_id"] is None

    async def test_invalid_transition_raises(self, tower: TowerController) -> None:
        with pytest.raises(InvalidTowerTransitionError):
            await tower._transition(TowerState.COMPLETE)

    async def test_valid_transitions(self, tower: TowerController) -> None:
        await tower._transition(TowerState.PLANNING)
        assert tower.state == TowerState.PLANNING

        await tower._transition(TowerState.MANAGING)
        assert tower.state == TowerState.MANAGING

        await tower._transition(TowerState.COMPLETE)
        assert tower.state == TowerState.COMPLETE

        await tower._transition(TowerState.IDLE)
        assert tower.state == TowerState.IDLE

    async def test_error_transition(self, tower: TowerController) -> None:
        await tower._transition(TowerState.PLANNING)
        await tower._transition(TowerState.ERROR)
        assert tower.state == TowerState.ERROR

        # Can recover to idle from error
        await tower._transition(TowerState.IDLE)
        assert tower.state == TowerState.IDLE

    async def test_reset(self, tower: TowerController) -> None:
        tower._state = TowerState.MANAGING
        tower._current_goal = "test"
        tower._current_project_id = "proj-1"
        await tower.reset()
        assert tower.state == TowerState.IDLE
        assert tower.current_goal is None
        assert tower.current_project_id is None


@patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="session-123")
@pytest.mark.asyncio
class TestSubmitGoal:
    async def test_submit_goal_success(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "test-proj", repo_path="/tmp/repo")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        result = await tower.submit_goal(project.id, "Build feature X")

        assert result["status"] == "accepted"
        assert result["project_id"] == project.id
        assert result["session_id"] == "session-123"
        assert result["context_package"]["goal"] == "Build feature X"
        assert result["context_package"]["project_name"] == "test-proj"
        assert result["context_package"]["repo_path"] == "/tmp/repo"
        assert tower.state == TowerState.MANAGING

    async def test_submit_goal_publishes_event(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        captured: list[dict] = []
        event_bus.subscribe("tower_goal_submitted", lambda d: captured.append(d))

        await tower.submit_goal(project.id, "Test goal")

        assert len(captured) == 1
        assert captured[0]["goal"] == "Test goal"
        assert captured[0]["session_id"] == "session-123"

    async def test_submit_goal_publishes_state_changes(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        states: list[str] = []
        event_bus.subscribe(
            "tower_state_changed", lambda d: states.append(d["new_state"])
        )

        await tower.submit_goal(project.id, "Test goal")

        assert "planning" in states
        assert "managing" in states

    async def test_submit_goal_project_not_found(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        tower = TowerController(db, event_bus)
        with pytest.raises(ValueError, match="not found"):
            await tower.submit_goal("nonexistent-id", "Some goal")
        # Tower should be in error state after failure
        assert tower.state == TowerState.ERROR

    async def test_submit_goal_while_busy(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        await tower.submit_goal(project.id, "First goal")
        assert tower.state == TowerState.MANAGING

        with pytest.raises(TowerBusyError):
            await tower.submit_goal(project.id, "Second goal")

    async def test_submit_goal_after_complete(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        """Can submit a new goal after previous one completes."""
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        await tower.submit_goal(project.id, "First goal")
        await tower.mark_complete()

        result = await tower.submit_goal(project.id, "Second goal")
        assert result["status"] == "accepted"

    async def test_submit_goal_after_error(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        """Can submit a new goal after a previous error."""
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        await tower.submit_goal(project.id, "First goal")
        tower._state = TowerState.ERROR  # simulate error

        result = await tower.submit_goal(project.id, "Recovery goal")
        assert result["status"] == "accepted"


@patch("atc.tower.controller.stop_leader", new_callable=AsyncMock)
@patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="session-123")
@pytest.mark.asyncio
class TestCancelGoal:
    async def test_cancel_active_goal(
        self, mock_start: AsyncMock, mock_stop: AsyncMock, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        await tower.submit_goal(project.id, "Cancel me")
        await tower.cancel_goal()

        mock_stop.assert_called_once()
        assert tower.state == TowerState.IDLE

    async def test_cancel_resets_properties(
        self, mock_start: AsyncMock, mock_stop: AsyncMock, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        await tower.submit_goal(project.id, "Cancel me")
        await tower.cancel_goal()

        assert tower.current_goal is None
        assert tower.current_project_id is None
        assert tower.current_session_id is None


@pytest.mark.asyncio
class TestSessionMonitoring:
    @patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="sess-1")
    async def test_leader_error_transitions_tower(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        await tower.submit_goal(project.id, "Watch me fail")
        assert tower.state == TowerState.MANAGING

        # Simulate leader session entering error state
        await event_bus.publish(
            "session_status_changed",
            {"session_id": "sess-1", "new_status": "error"},
        )

        assert tower.state == TowerState.ERROR

    @patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="sess-1")
    async def test_unrelated_session_error_ignored(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        await tower.submit_goal(project.id, "Stable goal")

        # Error on a different session should not affect tower
        await event_bus.publish(
            "session_status_changed",
            {"session_id": "other-session", "new_status": "error"},
        )

        assert tower.state == TowerState.MANAGING


@pytest.mark.asyncio
class TestWebSocketBroadcast:
    @patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="sess-1")
    async def test_state_changes_broadcast_to_ws(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        ws_hub = AsyncMock()
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus, ws_hub=ws_hub)

        await tower.submit_goal(project.id, "WS goal")

        # Should have broadcast at least planning and managing transitions
        calls = [c for c in ws_hub.broadcast.call_args_list if c[0][0] == "tower"]
        assert len(calls) >= 2
        states_broadcast = [c[0][1]["new_state"] for c in calls if "new_state" in c[0][1]]
        assert "planning" in states_broadcast
        assert "managing" in states_broadcast

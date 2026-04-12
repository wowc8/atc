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
    get_session,
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
        assert status["leader_session_id"] is None

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
        tower._leader_output_lines = ["line1", "line2"]
        await tower.reset()
        assert tower.state == TowerState.IDLE
        assert tower.current_goal is None
        assert tower.current_project_id is None
        assert tower._leader_output_lines == []

    async def test_get_status_includes_output_line_count(self, tower: TowerController) -> None:
        status = tower.get_status()
        assert status["output_line_count"] == 0

        tower._leader_output_lines = ["a", "b", "c"]
        status = tower.get_status()
        assert status["output_line_count"] == 3


@patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="leader-sess-123")
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
        assert result["leader_session_id"] == "leader-sess-123"
        assert result["context_package"]["goal"] == "Build feature X"
        assert result["context_package"]["project_name"] == "test-proj"
        assert result["context_package"]["repo_path"] == "/tmp/repo"
        assert tower.state == TowerState.MANAGING
        # Leader session tracked separately
        assert tower._leader_session_id == "leader-sess-123"

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
        assert captured[0]["session_id"] == "leader-sess-123"

    async def test_submit_goal_publishes_state_changes(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        states: list[str] = []
        event_bus.subscribe("tower_state_changed", lambda d: states.append(d["new_state"]))

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
@patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="leader-sess-123")
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

        # Leader should be stopped but Tower stays in managing
        mock_stop.assert_called_once()
        assert tower.current_goal is None
        assert tower._leader_session_id is None

    async def test_cancel_resets_goal_properties(
        self, mock_start: AsyncMock, mock_stop: AsyncMock, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        await tower.submit_goal(project.id, "Cancel me")
        await tower.cancel_goal()

        assert tower.current_goal is None
        assert tower._leader_session_id is None


@pytest.mark.asyncio
class TestSessionMonitoring:
    @patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="leader-sess-1")
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
            {"session_id": "leader-sess-1", "new_status": "error"},
        )

        assert tower.state == TowerState.ERROR

    @patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="leader-sess-1")
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
    @patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="leader-sess-1")
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


@patch(
    "atc.tower.controller.send_tower_message",
    new_callable=AsyncMock,
)
@patch("atc.tower.controller.start_tower_session", new_callable=AsyncMock, return_value="tower-sess-1")
@patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="leader-sess-1")
@pytest.mark.asyncio
class TestSendMessage:
    async def test_send_message_success(
        self, mock_start_leader: AsyncMock, mock_start_tower: AsyncMock,
        mock_send: AsyncMock, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        # Start tower session first, then submit goal
        await tower.start_session(project.id)
        await tower.send_message("Do the thing")

        mock_send.assert_called_once_with(
            db,
            "tower-sess-1",
            "Do the thing",
            event_bus=event_bus,
        )

    async def test_send_message_no_session_raises(
        self, mock_start_leader: AsyncMock, mock_start_tower: AsyncMock,
        mock_send: AsyncMock, db, event_bus: EventBus
    ) -> None:
        tower = TowerController(db, event_bus)
        with pytest.raises(ValueError, match="No active Tower session"):
            await tower.send_message("Hello")

    async def test_send_message_broadcasts_to_ws(
        self, mock_start_leader: AsyncMock, mock_start_tower: AsyncMock,
        mock_send: AsyncMock, db, event_bus: EventBus
    ) -> None:
        ws_hub = AsyncMock()
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus, ws_hub=ws_hub)

        await tower.start_session(project.id)
        ws_hub.broadcast.reset_mock()

        await tower.send_message("Do something")

        # Find the message_sent broadcast
        msg_calls = [
            c
            for c in ws_hub.broadcast.call_args_list
            if c[0][0] == "tower" and c[0][1].get("type") == "message_sent"
        ]
        assert len(msg_calls) == 1
        assert msg_calls[0][0][1]["message"] == "Do something"


@patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="leader-sess-1")
@pytest.mark.asyncio
class TestGetProgress:
    async def test_progress_no_active_goal(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        tower = TowerController(db, event_bus)
        progress = await tower.get_progress()
        assert progress["total"] == 0
        assert progress["all_done"] is False

    async def test_progress_with_tasks(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        import uuid

        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        await tower.submit_goal(project.id, "Build it")

        # Insert some task_graph rows
        for status in ["done", "done", "in_progress", "todo"]:
            await db.execute(
                "INSERT INTO task_graphs (id, project_id, title, status, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
                (str(uuid.uuid4()), project.id, f"Task-{status}", status),
            )
        await db.commit()

        progress = await tower.get_progress()
        assert progress["total"] == 4
        assert progress["done"] == 2
        assert progress["in_progress"] == 1
        assert progress["todo"] == 1
        assert progress["progress_pct"] == 50
        assert progress["all_done"] is False

    async def test_progress_all_done(self, mock_start: AsyncMock, db, event_bus: EventBus) -> None:
        import uuid

        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        await tower.submit_goal(project.id, "Build it")

        # Insert all-done tasks
        for _ in range(3):
            await db.execute(
                "INSERT INTO task_graphs (id, project_id, title, status, "
                "created_at, updated_at) VALUES "
                "(?, ?, ?, 'done', datetime('now'), datetime('now'))",
                (str(uuid.uuid4()), project.id, "Done task"),
            )
        await db.commit()

        progress = await tower.get_progress()
        assert progress["all_done"] is True
        assert progress["progress_pct"] == 100

    async def test_progress_broadcasts_to_ws(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        ws_hub = AsyncMock()
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus, ws_hub=ws_hub)

        await tower.submit_goal(project.id, "Build it")
        ws_hub.broadcast.reset_mock()

        await tower.get_progress()

        progress_calls = [
            c
            for c in ws_hub.broadcast.call_args_list
            if c[0][0] == "tower" and c[0][1].get("type") == "progress"
        ]
        assert len(progress_calls) == 1


@patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="leader-sess-1")
@pytest.mark.asyncio
class TestLeaderOutput:
    async def test_captures_leader_output(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        await tower.submit_goal(project.id, "Test goal")

        # Simulate PTY output from leader session
        await event_bus.publish(
            "pty_output",
            {"session_id": "leader-sess-1", "data": b"Working on task 1\n"},
        )

        assert len(tower._leader_output_lines) == 1
        assert "Working on task 1" in tower._leader_output_lines[0]

    async def test_ignores_other_session_output(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        await tower.submit_goal(project.id, "Test goal")

        # Output from a different session should be ignored
        await event_bus.publish(
            "pty_output",
            {"session_id": "other-session", "data": b"Not for Tower\n"},
        )

        assert len(tower._leader_output_lines) == 0

    async def test_output_ring_buffer(self, mock_start: AsyncMock, db, event_bus: EventBus) -> None:
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)
        tower._max_output_lines = 5

        await tower.submit_goal(project.id, "Test goal")

        # Send more lines than the buffer size
        for i in range(10):
            await event_bus.publish(
                "pty_output",
                {"session_id": "leader-sess-1", "data": f"Line {i}\n".encode()},
            )

        assert len(tower._leader_output_lines) == 5
        assert "Line 9" in tower._leader_output_lines[-1]

    async def test_output_broadcasts_activity_to_ws(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        ws_hub = AsyncMock()
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus, ws_hub=ws_hub)

        await tower.submit_goal(project.id, "Test goal")
        ws_hub.broadcast.reset_mock()

        await event_bus.publish(
            "pty_output",
            {"session_id": "leader-sess-1", "data": b"Thinking about it...\n"},
        )

        activity_calls = [
            c
            for c in ws_hub.broadcast.call_args_list
            if c[0][0] == "tower" and c[0][1].get("type") == "leader_activity"
        ]
        assert len(activity_calls) == 1
        assert "Thinking about it" in activity_calls[0][0][1]["preview"]

    async def test_output_handles_string_data(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        await tower.submit_goal(project.id, "Test goal")

        # String data (not bytes) should also work
        await event_bus.publish(
            "pty_output",
            {"session_id": "leader-sess-1", "data": "String output\n"},
        )

        assert len(tower._leader_output_lines) == 1

    async def test_mark_complete_clears_output(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        await tower.submit_goal(project.id, "Test goal")

        await event_bus.publish(
            "pty_output",
            {"session_id": "leader-sess-1", "data": b"Some output\n"},
        )
        assert len(tower._leader_output_lines) > 0

        await tower.mark_complete()
        assert tower._leader_output_lines == []

    async def test_cancel_clears_output(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        with patch("atc.tower.controller.stop_leader", new_callable=AsyncMock):
            project = await create_project(db, "test-proj")
            await create_leader(db, project.id)
            tower = TowerController(db, event_bus)

            await tower.submit_goal(project.id, "Test goal")
            tower._leader_output_lines = ["line1", "line2"]

            await tower.cancel_goal()
            assert tower._leader_output_lines == []

    async def test_empty_output_not_broadcast(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        ws_hub = AsyncMock()
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus, ws_hub=ws_hub)

        await tower.submit_goal(project.id, "Test goal")
        ws_hub.broadcast.reset_mock()

        # Empty/whitespace-only output should not trigger broadcast
        await event_bus.publish(
            "pty_output",
            {"session_id": "leader-sess-1", "data": b"   \n  \n"},
        )

        activity_calls = [
            c
            for c in ws_hub.broadcast.call_args_list
            if c[0][0] == "tower" and c[0][1].get("type") == "leader_activity"
        ]
        assert len(activity_calls) == 0


@patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="leader-sess-1")
@patch("atc.tower.controller.send_tower_message", new_callable=AsyncMock)
@pytest.mark.asyncio
class TestNotifyTowerGoalStarted:
    """Tests for _notify_tower_goal_started — ensure Tower's terminal gets notified."""

    async def test_no_session_returns_early(
        self, mock_send: AsyncMock, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        """If Tower has no active session, notification is a no-op."""
        project = await create_project(db, "my-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)
        # _current_session_id is None — no tower session started
        assert tower._current_session_id is None

        await tower._notify_tower_goal_started(project.id, "leader-sess-1", "Build X")

        mock_send.assert_not_called()

    async def test_sends_notification_when_session_active(
        self, mock_send: AsyncMock, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        """When Tower has an active session, a notification message is sent."""
        project = await create_project(db, "my-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)
        tower._current_session_id = "tower-sess-1"  # simulate active tower session

        # Skip the 3s sleep in tests
        import asyncio as _asyncio
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await tower._notify_tower_goal_started(project.id, "leader-sess-1", "Build X")

        mock_send.assert_called_once()
        call_args = mock_send.call_args
        # session_id passed as 2nd positional arg
        assert call_args[0][1] == "tower-sess-1"
        # message contains goal and leader session
        message = call_args[0][2]
        assert "Build X" in message
        assert "leader-sess-1" in message
        assert project.id in message

    async def test_notification_error_is_swallowed(
        self, mock_send: AsyncMock, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        """send_tower_message errors are caught so goal submission isn't disrupted."""
        project = await create_project(db, "my-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)
        tower._current_session_id = "tower-sess-1"
        mock_send.side_effect = ValueError("Tower session has no tmux pane")

        # Should not raise
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await tower._notify_tower_goal_started(project.id, "leader-sess-1", "Build X")

        mock_send.assert_called_once()


@pytest.mark.asyncio
class TestAuthBlockDetection:
    @patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="leader-sess-1")
    async def test_leader_auth_block_marks_session_error_and_transitions_tower(
        self, mock_start: AsyncMock, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "test-proj")
        await create_leader(db, project.id)
        tower = TowerController(db, event_bus)

        await tower.submit_goal(project.id, "Blocked goal")
        await db.execute(
            "INSERT INTO sessions (id, project_id, session_type, name, status, created_at, updated_at) VALUES (?, ?, 'manager', 'leader', 'working', datetime('now'), datetime('now'))",
            ("leader-sess-1", project.id),
        )
        await db.commit()

        await tower._on_agent_output({
            "session_id": "leader-sess-1",
            "data": "Not logged in · Please run /login\n".encode("utf-8"),
        })

        session = await get_session(db, "leader-sess-1")
        assert session is not None
        assert session.status == "error"
        assert tower.state == TowerState.ERROR

    async def test_extract_auth_blocker_detects_login_and_keychain_hints(self, tower: TowerController) -> None:
        assert tower._extract_auth_blocker("Not logged in · Please run /login") is not None
        assert tower._extract_auth_blocker("Run in another terminal: security unlock-keychain") is not None
        assert tower._extract_auth_blocker("All good, working normally") is None

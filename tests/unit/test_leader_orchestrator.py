"""Tests for Leader orchestrator — Ace spawning, task lifecycle, monitoring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from atc.core.events import EventBus
from atc.leader.orchestrator import AceAssignment, LeaderOrchestrator
from atc.state.db import (
    _SCHEMA_SQL,
    create_leader,
    create_project,
    create_task_graph,
    get_connection,
    get_task_graph,
    run_migrations,
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


@pytest.fixture(autouse=True)
def reset_global_ace_counter():
    """Reset the global Ace counter and lock before each test for isolation."""
    import atc.leader.orchestrator as orch_mod

    orch_mod._GLOBAL_ACTIVE_ACES = 0
    orch_mod._GLOBAL_LOCK = None
    yield
    orch_mod._GLOBAL_ACTIVE_ACES = 0
    orch_mod._GLOBAL_LOCK = None


@pytest.fixture
async def project_with_leader(db):
    """Create a project and leader, return (project, leader)."""
    project = await create_project(db, "test-proj", repo_path="/tmp/repo")
    leader = await create_leader(db, project.id, goal="Build auth")
    return project, leader


@pytest.fixture
def orchestrator(db, event_bus, project_with_leader) -> LeaderOrchestrator:
    project, leader = project_with_leader
    return LeaderOrchestrator(
        project_id=project.id,
        leader_id=leader.id,
        conn=db,
        event_bus=event_bus,
    )


# ---------------------------------------------------------------------------
# spawn_aces_for_ready_tasks
# ---------------------------------------------------------------------------


@patch("atc.leader.orchestrator.create_ace", new_callable=AsyncMock, return_value="ace-session-1")
@pytest.mark.asyncio
class TestSpawnAces:
    async def test_spawns_for_ready_tasks(
        self,
        mock_create: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        await create_project(db, "p2")
        # Use orchestrator's project_id
        await create_task_graph(db, orchestrator.project_id, "Login page")

        assignments = await orchestrator.spawn_aces_for_ready_tasks()

        assert len(assignments) == 1
        assert assignments[0].task_title == "Login page"
        assert assignments[0].ace_session_id == "ace-session-1"
        assert assignments[0].assignment_id != ""  # idempotency key set
        mock_create.assert_called_once()

    async def test_uses_project_agent_provider(
        self,
        mock_create: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        """Ace sessions must use the project's configured agent_provider."""
        # Set project to use opencode
        await db.execute(
            "UPDATE projects SET agent_provider = ? WHERE id = ?",
            ("opencode", orchestrator.project_id),
        )
        await db.commit()

        await create_task_graph(db, orchestrator.project_id, "Task A")
        await orchestrator.spawn_aces_for_ready_tasks()

        # Verify the launch_command used the opencode provider
        call_kwargs = mock_create.call_args
        assert call_kwargs is not None
        assert call_kwargs.kwargs.get("launch_command") == "opencode"

    async def test_default_provider_uses_claude(
        self,
        mock_create: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        """Default agent_provider (claude_code) uses the Claude launch command."""
        await create_task_graph(db, orchestrator.project_id, "Task B")
        await orchestrator.spawn_aces_for_ready_tasks()

        call_kwargs = mock_create.call_args
        assert call_kwargs is not None
        # get_launch_command returns the atc-agent wrapper script if it exists,
        # otherwise falls back to the bare claude command.
        from atc.agents.factory import get_launch_command
        assert call_kwargs.kwargs.get("launch_command") == get_launch_command("claude_code")

    async def test_skips_tasks_with_unmet_deps(
        self,
        mock_create: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        tg1 = await create_task_graph(db, orchestrator.project_id, "Task A")
        await create_task_graph(
            db,
            orchestrator.project_id,
            "Task B",
            dependencies=[tg1.id],
        )

        assignments = await orchestrator.spawn_aces_for_ready_tasks()

        # Only Task A should be spawned (Task B blocked on A)
        assert len(assignments) == 1
        assert assignments[0].task_title == "Task A"

    async def test_respects_max_concurrent_limit(
        self,
        mock_create: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        orchestrator._governor._max = 2

        for i in range(5):
            await create_task_graph(db, orchestrator.project_id, f"Task {i}")

        assignments = await orchestrator.spawn_aces_for_ready_tasks()

        assert len(assignments) == 2

    async def test_no_tasks_returns_empty(
        self,
        mock_create: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        assignments = await orchestrator.spawn_aces_for_ready_tasks()
        assert assignments == []

    async def test_skip_already_assigned(
        self,
        mock_create: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        tg = await create_task_graph(db, orchestrator.project_id, "Task A")

        # Manually add assignment
        orchestrator.assignments[tg.id] = AceAssignment(
            ace_session_id="existing-ace",
            task_graph_id=tg.id,
            task_title="Task A",
        )

        assignments = await orchestrator.spawn_aces_for_ready_tasks()
        assert len(assignments) == 0

    async def test_marks_task_in_progress(
        self,
        mock_create: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        tg = await create_task_graph(db, orchestrator.project_id, "Task A")

        await orchestrator.spawn_aces_for_ready_tasks()

        updated = await get_task_graph(db, tg.id)
        assert updated is not None
        assert updated.status == "in_progress"

    async def test_assigns_ace_to_task_graph(
        self,
        mock_create: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        tg = await create_task_graph(db, orchestrator.project_id, "Task A")

        await orchestrator.spawn_aces_for_ready_tasks()

        updated = await get_task_graph(db, tg.id)
        assert updated is not None
        assert updated.assigned_ace_id == "ace-session-1"

    async def test_publishes_ace_spawned_event(
        self,
        mock_create: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
        event_bus: EventBus,
    ) -> None:
        await create_task_graph(db, orchestrator.project_id, "Task A")

        captured: list[dict] = []
        event_bus.subscribe("leader_ace_spawned", lambda d: captured.append(d))

        await orchestrator.spawn_aces_for_ready_tasks()

        assert len(captured) == 1
        assert captured[0]["task_title"] == "Task A"
        assert captured[0]["session_id"] == "ace-session-1"
        assert "assignment_id" in captured[0]

    async def test_spawn_failure_does_not_crash(
        self,
        mock_create: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        mock_create.side_effect = RuntimeError("tmux failed")
        await create_task_graph(db, orchestrator.project_id, "Task A")

        # Should not raise
        assignments = await orchestrator.spawn_aces_for_ready_tasks()
        assert len(assignments) == 0

    async def test_idempotent_spawn_same_task(
        self,
        mock_create: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        """Spawning the same task twice with the same leader is idempotent."""
        await create_task_graph(db, orchestrator.project_id, "Task A")

        # First spawn
        assignments1 = await orchestrator.spawn_aces_for_ready_tasks()
        assert len(assignments1) == 1

        # Second call -- task is already in self.assignments, so skipped
        assignments2 = await orchestrator.spawn_aces_for_ready_tasks()
        assert len(assignments2) == 0

    async def test_counter_not_inflated_for_already_assigned(
        self,
        mock_create: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        """Pre-reserved slots must not be counted for already-assigned tasks."""
        import atc.leader.orchestrator as orch_mod

        tg1 = await create_task_graph(db, orchestrator.project_id, "Task A")
        await create_task_graph(db, orchestrator.project_id, "Task B")

        # Pre-assign the first task
        orchestrator.assignments[tg1.id] = AceAssignment(
            ace_session_id="existing-ace",
            task_graph_id=tg1.id,
            task_title="Task A",
        )

        assignments = await orchestrator.spawn_aces_for_ready_tasks()

        assert len(assignments) == 1
        assert assignments[0].task_title == "Task B"
        # Counter should reflect exactly 1 active Ace, not 2
        assert orch_mod._GLOBAL_ACTIVE_ACES == 1

    async def test_counter_decremented_on_spawn_failure(
        self,
        mock_create: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        """Counter must return to zero after a failed spawn."""
        import atc.leader.orchestrator as orch_mod

        mock_create.side_effect = RuntimeError("tmux exploded")
        await create_task_graph(db, orchestrator.project_id, "Task A")

        assignments = await orchestrator.spawn_aces_for_ready_tasks()
        assert len(assignments) == 0
        assert orch_mod._GLOBAL_ACTIVE_ACES == 0

    async def test_deploy_uses_real_session_id_for_staging_root(
        self,
        mock_create: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        await create_task_graph(db, orchestrator.project_id, "Task Deploy")

        assignments = await orchestrator.spawn_aces_for_ready_tasks()

        assert len(assignments) == 1
        assert assignments[0].deployed_root == Path("/tmp/atc-agents/ace-session-1")

        call_kwargs = mock_create.call_args
        assert call_kwargs is not None
        deploy_kwargs = call_kwargs.kwargs.get("deploy_spec_kwargs")
        assert deploy_kwargs is not None
        assert deploy_kwargs["task_title"] == "Task Deploy"
        assert deploy_kwargs["project_id"] == orchestrator.project_id


# ---------------------------------------------------------------------------
# send_instruction_to_ace
# ---------------------------------------------------------------------------


@patch("atc.leader.orchestrator.start_ace", new_callable=AsyncMock)
@pytest.mark.asyncio
class TestSendInstruction:
    async def test_sends_instruction(
        self,
        mock_start: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        tg = await create_task_graph(db, orchestrator.project_id, "Task A")

        # Manually create session row for the Ace
        from atc.state import db as db_ops

        session = await db_ops.create_session(
            db,
            orchestrator.project_id,
            "ace",
            "ace-task-a",
        )

        orchestrator.assignments[tg.id] = AceAssignment(
            ace_session_id=session.id,
            task_graph_id=tg.id,
            task_title="Task A",
        )

        await orchestrator.send_instruction_to_ace(tg.id, "Build the login page")

        mock_start.assert_called_once_with(
            db,
            session.id,
            instruction="Build the login page",
            event_bus=orchestrator.event_bus,
        )
        assert orchestrator.assignments[tg.id].status == "working"

    async def test_instruction_to_unknown_task_raises(
        self,
        mock_start: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        with pytest.raises(ValueError, match="No Ace assigned"):
            await orchestrator.send_instruction_to_ace("nonexistent", "Do something")


@pytest.mark.asyncio
class TestSpawnRetryAssignmentReuse:
    async def test_reuses_terminal_assignment_without_todo_to_in_progress_jump(
        self,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        from atc.state import db as db_ops

        tg = await create_task_graph(db, orchestrator.project_id, "Task Retry")
        first, created = await db_ops.assign_task(db, tg.id, "ace-old", f"{orchestrator.leader_id}:{tg.id}")
        assert created is True
        updated = await db_ops.update_task_assignment_status(db, first.assignment_id, "working")
        assert updated is not None
        updated = await db_ops.update_task_assignment_status(db, first.assignment_id, "failed")
        assert updated is not None
        await db_ops.update_task_graph_status(db, tg.id, "error")
        await db_ops.update_task_graph_status(db, tg.id, "todo")

        orchestrator.assignments.clear()

        with (
            patch("atc.leader.orchestrator.create_ace", new=AsyncMock(return_value="ace-new")),
            patch("atc.leader.orchestrator.get_launch_command", return_value="claude"),
            patch("atc.leader.orchestrator.build_context_package", new=AsyncMock(return_value={"context_entries": []})),
        ):
            assignment = await orchestrator._spawn_ace_for_task(tg.id, tg.title, tg.description)

        assert assignment is not None
        refreshed = await db_ops.get_task_graph(db, tg.id)
        assert refreshed is not None
        assert refreshed.status == "in_progress"
        assert refreshed.assigned_ace_id == "ace-new"


# ---------------------------------------------------------------------------
# mark_task_done
# ---------------------------------------------------------------------------


@patch("atc.leader.orchestrator.destroy_ace", new_callable=AsyncMock)
@pytest.mark.asyncio
class TestMarkTaskDone:
    async def test_marks_done_and_destroys_ace(
        self,
        mock_destroy: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
        event_bus: EventBus,
    ) -> None:
        tg = await create_task_graph(db, orchestrator.project_id, "Task A")
        # Transition through the state machine: todo -> assigned -> in_progress
        from atc.state import db as db_ops

        await db_ops.update_task_graph_status(db, tg.id, "assigned")
        await db_ops.update_task_graph_status(db, tg.id, "in_progress")

        orchestrator.assignments[tg.id] = AceAssignment(
            ace_session_id="ace-1",
            task_graph_id=tg.id,
            task_title="Task A",
            status="working",
        )

        captured: list[dict] = []
        event_bus.subscribe("leader_task_completed", lambda d: captured.append(d))

        await orchestrator.mark_task_done(tg.id)

        updated = await get_task_graph(db, tg.id)
        assert updated is not None
        assert updated.status == "done"
        assert orchestrator.assignments[tg.id].status == "done"
        mock_destroy.assert_called_once()
        assert len(captured) == 1

    async def test_done_without_assignment(
        self,
        mock_destroy: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        tg = await create_task_graph(db, orchestrator.project_id, "Task A")
        from atc.state import db as db_ops

        await db_ops.update_task_graph_status(db, tg.id, "assigned")
        await db_ops.update_task_graph_status(db, tg.id, "in_progress")

        # Should not crash even without assignment
        await orchestrator.mark_task_done(tg.id)

        updated = await get_task_graph(db, tg.id)
        assert updated is not None
        assert updated.status == "done"
        mock_destroy.assert_not_called()

    async def test_counter_decremented_even_without_assignment_in_memory(
        self,
        mock_destroy: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        """Bug #163: counter must decrement even when orchestrator has no in-memory assignment.

        This covers the case where the server restarted / a fresh orchestrator
        was created after aces were spawned by a previous instance.
        """
        import atc.leader.orchestrator as orch_mod
        from atc.state import db as db_ops
        tg = await create_task_graph(db, orchestrator.project_id, "Task A")
        # Advance through state machine so mark_task_done can mark it done
        await db_ops.update_task_graph_status(db, tg.id, "assigned")
        await db_ops.update_task_graph_status(db, tg.id, "in_progress")

        # Manually bump the global counter (simulating a spawned ace)
        orch_mod._GLOBAL_ACTIVE_ACES = 1
        # NOTE: orchestrator.assignments is empty (fresh instance, simulating restart)

        await orchestrator.mark_task_done(tg.id)

        assert orch_mod._GLOBAL_ACTIVE_ACES == 0


# ---------------------------------------------------------------------------
# mark_task_failed
# ---------------------------------------------------------------------------


@patch("atc.leader.orchestrator.destroy_ace", new_callable=AsyncMock)
@pytest.mark.asyncio
class TestMarkTaskFailed:
    async def test_resets_to_todo_and_destroys_ace(
        self,
        mock_destroy: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
        event_bus: EventBus,
    ) -> None:
        tg = await create_task_graph(db, orchestrator.project_id, "Task A")
        from atc.state import db as db_ops

        await db_ops.update_task_graph_status(db, tg.id, "assigned")
        await db_ops.update_task_graph_status(db, tg.id, "in_progress")

        orchestrator.assignments[tg.id] = AceAssignment(
            ace_session_id="ace-1",
            task_graph_id=tg.id,
            task_title="Task A",
            status="working",
        )

        captured: list[dict] = []
        event_bus.subscribe("leader_task_failed", lambda d: captured.append(d))

        await orchestrator.mark_task_failed(tg.id, reason="Test failure")

        updated = await get_task_graph(db, tg.id)
        assert updated is not None
        assert updated.status == "todo"  # Reset for retry (via error -> todo)
        assert tg.id not in orchestrator.assignments  # Assignment removed
        mock_destroy.assert_called_once()
        assert len(captured) == 1
        assert captured[0]["reason"] == "Test failure"


# ---------------------------------------------------------------------------
# get_progress
# ---------------------------------------------------------------------------


    async def test_failed_counter_decremented_without_in_memory_assignment(
        self,
        mock_destroy: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        """Bug #163: mark_task_failed must also decrement counter unconditionally."""
        import atc.leader.orchestrator as orch_mod
        tg = await create_task_graph(db, orchestrator.project_id, "Task Fail")

        orch_mod._GLOBAL_ACTIVE_ACES = 2
        # No in-memory assignment

        await orchestrator.mark_task_failed(tg.id)

        assert orch_mod._GLOBAL_ACTIVE_ACES == 1  # decremented once


@pytest.mark.asyncio
class TestGetProgress:
    async def test_progress_with_tasks(self, db, orchestrator: LeaderOrchestrator) -> None:
        tg1 = await create_task_graph(db, orchestrator.project_id, "Done Task")
        from atc.state import db as db_ops

        await db_ops.update_task_graph_status(db, tg1.id, "assigned")
        await db_ops.update_task_graph_status(db, tg1.id, "in_progress")
        await db_ops.update_task_graph_status(db, tg1.id, "done")

        tg2 = await create_task_graph(db, orchestrator.project_id, "In Progress")
        await db_ops.update_task_graph_status(db, tg2.id, "assigned")
        await db_ops.update_task_graph_status(db, tg2.id, "in_progress")

        await create_task_graph(db, orchestrator.project_id, "Todo Task")

        progress = await orchestrator.get_progress()

        assert progress["total"] == 3
        assert progress["done"] == 1
        assert progress["in_progress"] == 1
        assert progress["todo"] == 1
        assert progress["all_done"] is False
        assert progress["progress_pct"] == 33
        assert progress["leader_id"] == orchestrator.leader_id

    async def test_progress_empty(self, db, orchestrator: LeaderOrchestrator) -> None:
        progress = await orchestrator.get_progress()
        assert progress["total"] == 0
        assert progress["done"] == 0
        assert progress["in_progress"] == 0
        assert progress["todo"] == 0
        assert progress["error"] == 0
        assert progress["progress_pct"] == 0
        assert progress["all_done"] is False


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


@patch("atc.leader.orchestrator.destroy_ace", new_callable=AsyncMock)
@pytest.mark.asyncio
class TestCleanup:
    async def test_destroys_active_aces(
        self,
        mock_destroy: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        orchestrator.assignments["tg-1"] = AceAssignment(
            ace_session_id="ace-1",
            task_graph_id="tg-1",
            task_title="Task 1",
            status="working",
        )
        orchestrator.assignments["tg-2"] = AceAssignment(
            ace_session_id="ace-2",
            task_graph_id="tg-2",
            task_title="Task 2",
            status="assigned",
        )
        orchestrator.assignments["tg-3"] = AceAssignment(
            ace_session_id="ace-3",
            task_graph_id="tg-3",
            task_title="Task 3",
            status="done",
        )

        await orchestrator.cleanup()

        # Only active sessions (assigned/working) should be destroyed
        assert mock_destroy.call_count == 2
        assert len(orchestrator.assignments) == 0

    async def test_cleanup_with_no_assignments(
        self,
        mock_destroy: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        await orchestrator.cleanup()
        mock_destroy.assert_not_called()


# ---------------------------------------------------------------------------
# on_session_status_changed (event monitoring)
# ---------------------------------------------------------------------------


@patch("atc.leader.orchestrator.destroy_ace", new_callable=AsyncMock)
@pytest.mark.asyncio
class TestSessionMonitoring:
    async def test_ace_error_triggers_task_failure(
        self,
        mock_destroy: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        tg = await create_task_graph(db, orchestrator.project_id, "Task A")
        from atc.state import db as db_ops

        await db_ops.update_task_graph_status(db, tg.id, "assigned")
        await db_ops.update_task_graph_status(db, tg.id, "in_progress")

        orchestrator.assignments[tg.id] = AceAssignment(
            ace_session_id="ace-1",
            task_graph_id=tg.id,
            task_title="Task A",
            status="working",
        )

        await orchestrator.on_session_status_changed(
            {
                "session_id": "ace-1",
                "new_status": "error",
            }
        )

        assert tg.id not in orchestrator.assignments
        updated = await get_task_graph(db, tg.id)
        assert updated is not None
        assert updated.status == "todo"

    async def test_ace_disconnected_triggers_task_failure(
        self,
        mock_destroy: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        tg = await create_task_graph(db, orchestrator.project_id, "Task A")
        from atc.state import db as db_ops

        await db_ops.update_task_graph_status(db, tg.id, "assigned")
        await db_ops.update_task_graph_status(db, tg.id, "in_progress")

        orchestrator.assignments[tg.id] = AceAssignment(
            ace_session_id="ace-1",
            task_graph_id=tg.id,
            task_title="Task A",
            status="working",
        )

        await orchestrator.on_session_status_changed(
            {
                "session_id": "ace-1",
                "new_status": "disconnected",
            }
        )

        assert tg.id not in orchestrator.assignments

    async def test_unrelated_session_ignored(
        self,
        mock_destroy: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        tg = await create_task_graph(db, orchestrator.project_id, "Task A")

        orchestrator.assignments[tg.id] = AceAssignment(
            ace_session_id="ace-1",
            task_graph_id=tg.id,
            task_title="Task A",
            status="working",
        )

        await orchestrator.on_session_status_changed(
            {
                "session_id": "other-session",
                "new_status": "error",
            }
        )

        # Assignment should be unchanged
        assert tg.id in orchestrator.assignments
        assert orchestrator.assignments[tg.id].status == "working"

    async def test_non_error_status_ignored(
        self,
        mock_destroy: AsyncMock,
        db,
        orchestrator: LeaderOrchestrator,
    ) -> None:
        tg = await create_task_graph(db, orchestrator.project_id, "Task A")

        orchestrator.assignments[tg.id] = AceAssignment(
            ace_session_id="ace-1",
            task_graph_id=tg.id,
            task_title="Task A",
            status="working",
        )

        await orchestrator.on_session_status_changed(
            {
                "session_id": "ace-1",
                "new_status": "waiting",
            }
        )

        # Should not trigger failure
        assert tg.id in orchestrator.assignments
        assert orchestrator.assignments[tg.id].status == "working"

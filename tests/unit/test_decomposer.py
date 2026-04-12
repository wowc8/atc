"""Tests for Leader goal decomposer."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from atc.leader.decomposer import (
    DecompositionResult,
    TaskSpec,
    decompose_goal,
    get_completion_status,
    get_ready_tasks,
)
from atc.state.db import (
    _SCHEMA_SQL,
    create_project,
    create_task_graph,
    get_connection,
    get_task_graph,
    list_task_graphs,
    run_migrations,
)
from atc.state.models import TaskGraph


@pytest.fixture
async def db():
    """In-memory database with schema applied."""
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


# ---------------------------------------------------------------------------
# decompose_goal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDecomposeGoal:
    async def test_basic_decomposition(self, db) -> None:
        project = await create_project(db, "test-proj")
        context = {"project_id": project.id, "goal": "Build auth system"}
        specs = [
            TaskSpec(title="Login page", description="Build login UI"),
            TaskSpec(title="Auth API", description="Build auth endpoints"),
        ]

        result = await decompose_goal(db, context, specs)

        assert result.error is None
        assert result.project_id == project.id
        assert result.goal == "Build auth system"
        assert len(result.task_graphs) == 2
        assert result.task_graphs[0].title == "Login page"
        assert result.task_graphs[1].title == "Auth API"
        assert result.task_graphs[0].status == "todo"

    async def test_decomposition_with_dependencies(self, db) -> None:
        project = await create_project(db, "test-proj")
        context = {"project_id": project.id, "goal": "Build feature"}
        specs = [
            TaskSpec(title="Task A"),
            TaskSpec(title="Task B", dependencies=["Task A"]),
            TaskSpec(title="Task C", dependencies=["Task A", "Task B"]),
        ]

        result = await decompose_goal(db, context, specs)

        assert result.error is None
        assert len(result.task_graphs) == 3

        # Task B should depend on Task A's ID
        task_b = result.task_graphs[1]
        assert task_b.dependencies is not None
        assert result.task_graphs[0].id in task_b.dependencies

        # Task C should depend on both A and B
        task_c = result.task_graphs[2]
        assert task_c.dependencies is not None
        assert len(task_c.dependencies) == 2
        assert result.task_graphs[0].id in task_c.dependencies
        assert result.task_graphs[1].id in task_c.dependencies

    async def test_decomposition_missing_project_id(self, db) -> None:
        context = {"goal": "Build something"}
        specs = [TaskSpec(title="Task A")]

        result = await decompose_goal(db, context, specs)

        assert result.error == "Context package missing project_id"

    async def test_decomposition_empty_specs(self, db) -> None:
        project = await create_project(db, "test-proj")
        context = {"project_id": project.id, "goal": "Build something"}

        result = await decompose_goal(db, context, [])

        assert result.error == "No task specifications provided"

    async def test_decomposition_project_not_found(self, db) -> None:
        context = {"project_id": "nonexistent", "goal": "Build something"}
        specs = [TaskSpec(title="Task A")]

        result = await decompose_goal(db, context, specs)

        assert result.error is not None
        assert "not found" in result.error

    async def test_decomposition_unknown_dependency_skipped(self, db) -> None:
        project = await create_project(db, "test-proj")
        context = {"project_id": project.id, "goal": "Build feature"}
        specs = [
            TaskSpec(title="Task A", dependencies=["Nonexistent Task"]),
        ]

        result = await decompose_goal(db, context, specs)

        assert result.error is None
        # Unknown dependency should be skipped (not crash)
        task_a = result.task_graphs[0]
        assert task_a.dependencies is None  # No valid deps resolved

    async def test_tasks_persisted_to_db(self, db) -> None:
        project = await create_project(db, "test-proj")
        context = {"project_id": project.id, "goal": "Build feature"}
        specs = [
            TaskSpec(title="Persist Me", description="Should be in DB"),
        ]

        result = await decompose_goal(db, context, specs)

        # Verify it's in the database
        tg = await get_task_graph(db, result.task_graphs[0].id)
        assert tg is not None
        assert tg.title == "Persist Me"
        assert tg.description == "Should be in DB"
        assert tg.project_id == project.id

    async def test_decomposition_multiple_with_descriptions(self, db) -> None:
        project = await create_project(db, "test-proj")
        context = {"project_id": project.id, "goal": "Full feature"}
        specs = [
            TaskSpec(title="T1", description="First task", priority=1),
            TaskSpec(title="T2", description="Second task", priority=2),
            TaskSpec(title="T3", description="Third task", priority=0),
        ]

        result = await decompose_goal(db, context, specs)

        assert len(result.task_graphs) == 3
        all_in_db = await list_task_graphs(db, project_id=project.id)
        assert len(all_in_db) == 3


# ---------------------------------------------------------------------------
# get_ready_tasks
# ---------------------------------------------------------------------------


class TestGetReadyTasks:
    def _make_tg(
        self, id: str, status: str = "todo", dependencies: list[str] | None = None,
    ) -> TaskGraph:
        return TaskGraph(
            id=id,
            project_id="proj-1",
            title=f"Task {id}",
            status=status,
            dependencies=dependencies,
        )

    def test_no_tasks(self) -> None:
        assert get_ready_tasks([]) == []

    def test_all_ready_no_deps(self) -> None:
        tasks = [self._make_tg("1"), self._make_tg("2")]
        ready = get_ready_tasks(tasks)
        assert len(ready) == 2

    def test_skip_non_todo(self) -> None:
        tasks = [
            self._make_tg("1", status="in_progress"),
            self._make_tg("2", status="done"),
            self._make_tg("3", status="todo"),
        ]
        ready = get_ready_tasks(tasks)
        assert len(ready) == 1
        assert ready[0].id == "3"

    def test_blocked_by_dependency(self) -> None:
        tasks = [
            self._make_tg("1", status="todo"),
            self._make_tg("2", status="todo", dependencies=["1"]),
        ]
        ready = get_ready_tasks(tasks)
        assert len(ready) == 1
        assert ready[0].id == "1"

    def test_unblocked_when_dep_done(self) -> None:
        tasks = [
            self._make_tg("1", status="done"),
            self._make_tg("2", status="todo", dependencies=["1"]),
        ]
        ready = get_ready_tasks(tasks)
        assert len(ready) == 1
        assert ready[0].id == "2"

    def test_partially_blocked(self) -> None:
        tasks = [
            self._make_tg("1", status="done"),
            self._make_tg("2", status="in_progress"),
            self._make_tg("3", status="todo", dependencies=["1", "2"]),
        ]
        ready = get_ready_tasks(tasks)
        # Task 3 blocked because task 2 is not done
        assert len(ready) == 0

    def test_chain_dependency(self) -> None:
        tasks = [
            self._make_tg("1", status="done"),
            self._make_tg("2", status="done", dependencies=["1"]),
            self._make_tg("3", status="todo", dependencies=["2"]),
        ]
        ready = get_ready_tasks(tasks)
        assert len(ready) == 1
        assert ready[0].id == "3"

    def test_empty_dependencies_treated_as_ready(self) -> None:
        tasks = [self._make_tg("1", status="todo", dependencies=[])]
        ready = get_ready_tasks(tasks)
        assert len(ready) == 1


# ---------------------------------------------------------------------------
# get_completion_status
# ---------------------------------------------------------------------------


class TestGetCompletionStatus:
    def _make_tg(self, id: str, status: str = "todo") -> TaskGraph:
        return TaskGraph(
            id=id,
            project_id="proj-1",
            title=f"Task {id}",
            status=status,
        )

    def test_empty(self) -> None:
        status = get_completion_status([])
        assert status["total"] == 0
        assert status["error"] == 0
        assert status["all_done"] is False
        assert status["progress_pct"] == 0

    def test_all_done(self) -> None:
        tasks = [self._make_tg("1", "done"), self._make_tg("2", "done")]
        status = get_completion_status(tasks)
        assert status["total"] == 2
        assert status["done"] == 2
        assert status["all_done"] is True
        assert status["progress_pct"] == 100

    def test_partial_progress(self) -> None:
        tasks = [
            self._make_tg("1", "done"),
            self._make_tg("2", "in_progress"),
            self._make_tg("3", "todo"),
            self._make_tg("4", "todo"),
        ]
        status = get_completion_status(tasks)
        assert status["total"] == 4
        assert status["done"] == 1
        assert status["in_progress"] == 1
        assert status["todo"] == 2
        assert status["all_done"] is False
        assert status["progress_pct"] == 25

    def test_none_done(self) -> None:
        tasks = [self._make_tg("1", "todo"), self._make_tg("2", "todo")]
        status = get_completion_status(tasks)
        assert status["done"] == 0
        assert status["all_done"] is False
        assert status["progress_pct"] == 0

    async def test_decompose_is_idempotent_replaces_todo_tasks(self, db) -> None:
        """Bug #164: calling decompose twice should replace, not duplicate."""
        project = await create_project(db, "test-proj")
        context = {"project_id": project.id, "goal": "Build feature"}

        # First call — creates 2 tasks
        await decompose_goal(db, context, [
            TaskSpec(title="Task A"),
            TaskSpec(title="Task B"),
        ])
        tasks_after_first = await list_task_graphs(db, project_id=project.id)
        assert len(tasks_after_first) == 2

        # Second call with different specs — should replace, not add
        result = await decompose_goal(db, context, [
            TaskSpec(title="Task C"),
            TaskSpec(title="Task D"),
            TaskSpec(title="Task E"),
        ])
        tasks_after_second = await list_task_graphs(db, project_id=project.id)
        # Should have exactly 3, not 5
        assert len(tasks_after_second) == 3
        assert len(result.task_graphs) == 3
        titles = {t.title for t in tasks_after_second}
        assert titles == {"Task C", "Task D", "Task E"}

    async def test_decompose_reuses_matching_todo_task_ids(self, db) -> None:
        """Repeated decomposition should preserve IDs for unchanged todo tasks."""
        project = await create_project(db, "test-proj")
        context = {"project_id": project.id, "goal": "Build feature"}

        first = await decompose_goal(db, context, [
            TaskSpec(title="Task A", description="first description"),
            TaskSpec(title="Task B", description="keep me"),
        ])
        first_ids = {tg.title: tg.id for tg in first.task_graphs}

        second = await decompose_goal(db, context, [
            TaskSpec(title="Task A", description="updated description"),
            TaskSpec(title="Task C", description="new task"),
        ])
        second_by_title = {tg.title: tg for tg in second.task_graphs}

        assert second_by_title["Task A"].id == first_ids["Task A"]
        assert second_by_title["Task A"].description == "updated description"
        assert second_by_title["Task C"].id != first_ids["Task A"]

        all_tasks = await list_task_graphs(db, project_id=project.id)
        titles = {t.title for t in all_tasks}
        assert titles == {"Task A", "Task C"}

    async def test_decompose_does_not_delete_assigned_tasks(self, db) -> None:
        """Bug #164: assigned tasks (ace running) should not be wiped by decompose."""
        from atc.state.db import update_task_graph, update_task_graph_status
        project = await create_project(db, "test-proj")
        context = {"project_id": project.id, "goal": "Build feature"}

        # Create a task and mark it assigned (simulate an ace is working on it)
        await decompose_goal(db, context, [TaskSpec(title="In Progress")])
        tasks = await list_task_graphs(db, project_id=project.id)
        assert len(tasks) == 1
        # Simulate assignment: set status + assigned_ace_id
        await update_task_graph_status(db, tasks[0].id, "assigned")
        await update_task_graph(db, tasks[0].id, assigned_ace_id="some-ace")

        # Re-decompose — should NOT delete the assigned task
        result = await decompose_goal(db, context, [TaskSpec(title="New Task")])
        all_tasks = await list_task_graphs(db, project_id=project.id)
        # assigned task preserved + new task created
        assert len(all_tasks) == 2
        statuses = {t.status for t in all_tasks}
        assert "assigned" in statuses
        assert "todo" in statuses

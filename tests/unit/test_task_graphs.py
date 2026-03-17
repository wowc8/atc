"""Tests for task_graph CRUD operations and status transitions."""

from __future__ import annotations

import pytest

from atc.state.db import (
    _SCHEMA_SQL,
    create_project,
    create_task_graph,
    delete_task_graph,
    get_connection,
    get_task_graph,
    list_task_graphs,
    update_task_graph,
    update_task_graph_status,
)
from atc.state.db import (
    run_migrations as async_run_migrations,
)


@pytest.fixture
async def db():
    """Provide an in-memory database with schema applied."""
    await async_run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.mark.asyncio
class TestTaskGraphCRUD:
    async def test_create_and_get(self, db) -> None:
        project = await create_project(db, "p1")
        tg = await create_task_graph(db, project.id, "Build feature X")
        assert tg.title == "Build feature X"
        assert tg.status == "todo"
        assert tg.project_id == project.id
        assert tg.description is None
        assert tg.assigned_ace_id is None
        assert tg.dependencies is None

        fetched = await get_task_graph(db, tg.id)
        assert fetched is not None
        assert fetched.id == tg.id
        assert fetched.title == "Build feature X"

    async def test_create_with_all_fields(self, db) -> None:
        project = await create_project(db, "p1")
        tg = await create_task_graph(
            db,
            project.id,
            "Deploy service",
            description="Deploy the service to production",
            status="todo",
            assigned_ace_id="ace-123",
            dependencies=["task-a", "task-b"],
        )
        assert tg.description == "Deploy the service to production"
        assert tg.assigned_ace_id == "ace-123"
        assert tg.dependencies == ["task-a", "task-b"]

    async def test_create_invalid_status(self, db) -> None:
        project = await create_project(db, "p1")
        with pytest.raises(ValueError, match="Invalid status"):
            await create_task_graph(db, project.id, "Bad", status="invalid")

    async def test_get_nonexistent(self, db) -> None:
        result = await get_task_graph(db, "does-not-exist")
        assert result is None

    async def test_list_empty(self, db) -> None:
        project = await create_project(db, "p1")
        items = await list_task_graphs(db, project_id=project.id)
        assert items == []

    async def test_list_by_project(self, db) -> None:
        p1 = await create_project(db, "p1")
        p2 = await create_project(db, "p2")
        await create_task_graph(db, p1.id, "Task A")
        await create_task_graph(db, p1.id, "Task B")
        await create_task_graph(db, p2.id, "Task C")

        p1_tasks = await list_task_graphs(db, project_id=p1.id)
        assert len(p1_tasks) == 2

        p2_tasks = await list_task_graphs(db, project_id=p2.id)
        assert len(p2_tasks) == 1

    async def test_list_all(self, db) -> None:
        p1 = await create_project(db, "p1")
        p2 = await create_project(db, "p2")
        await create_task_graph(db, p1.id, "Task A")
        await create_task_graph(db, p2.id, "Task B")

        all_tasks = await list_task_graphs(db)
        assert len(all_tasks) == 2

    async def test_update_title(self, db) -> None:
        project = await create_project(db, "p1")
        tg = await create_task_graph(db, project.id, "Old title")
        updated = await update_task_graph(db, tg.id, title="New title")
        assert updated is not None
        assert updated.title == "New title"

    async def test_update_description(self, db) -> None:
        project = await create_project(db, "p1")
        tg = await create_task_graph(db, project.id, "Task", description="Old desc")
        updated = await update_task_graph(db, tg.id, description="New desc")
        assert updated is not None
        assert updated.description == "New desc"

    async def test_update_clear_description(self, db) -> None:
        project = await create_project(db, "p1")
        tg = await create_task_graph(db, project.id, "Task", description="Has desc")
        updated = await update_task_graph(db, tg.id, description=None)
        assert updated is not None
        assert updated.description is None

    async def test_update_assigned_ace_id(self, db) -> None:
        project = await create_project(db, "p1")
        tg = await create_task_graph(db, project.id, "Task")
        updated = await update_task_graph(db, tg.id, assigned_ace_id="ace-456")
        assert updated is not None
        assert updated.assigned_ace_id == "ace-456"

    async def test_update_dependencies(self, db) -> None:
        project = await create_project(db, "p1")
        tg = await create_task_graph(db, project.id, "Task")
        updated = await update_task_graph(db, tg.id, dependencies=["dep-1", "dep-2"])
        assert updated is not None
        assert updated.dependencies == ["dep-1", "dep-2"]

    async def test_update_nonexistent(self, db) -> None:
        result = await update_task_graph(db, "nope", title="X")
        assert result is None

    async def test_update_no_changes(self, db) -> None:
        project = await create_project(db, "p1")
        tg = await create_task_graph(db, project.id, "Task")
        result = await update_task_graph(db, tg.id)
        assert result is not None
        assert result.title == "Task"

    async def test_delete(self, db) -> None:
        project = await create_project(db, "p1")
        tg = await create_task_graph(db, project.id, "To delete")
        deleted = await delete_task_graph(db, tg.id)
        assert deleted is True
        assert await get_task_graph(db, tg.id) is None

    async def test_delete_nonexistent(self, db) -> None:
        deleted = await delete_task_graph(db, "nope")
        assert deleted is False


@pytest.mark.asyncio
class TestTaskGraphStatusTransitions:
    async def test_todo_to_in_progress(self, db) -> None:
        project = await create_project(db, "p1")
        tg = await create_task_graph(db, project.id, "Task")
        updated = await update_task_graph_status(db, tg.id, "in_progress")
        assert updated is not None
        assert updated.status == "in_progress"

    async def test_todo_to_done(self, db) -> None:
        project = await create_project(db, "p1")
        tg = await create_task_graph(db, project.id, "Task")
        updated = await update_task_graph_status(db, tg.id, "done")
        assert updated is not None
        assert updated.status == "done"

    async def test_in_progress_to_done(self, db) -> None:
        project = await create_project(db, "p1")
        tg = await create_task_graph(db, project.id, "Task")
        await update_task_graph_status(db, tg.id, "in_progress")
        updated = await update_task_graph_status(db, tg.id, "done")
        assert updated is not None
        assert updated.status == "done"

    async def test_in_progress_to_todo(self, db) -> None:
        project = await create_project(db, "p1")
        tg = await create_task_graph(db, project.id, "Task")
        await update_task_graph_status(db, tg.id, "in_progress")
        updated = await update_task_graph_status(db, tg.id, "todo")
        assert updated is not None
        assert updated.status == "todo"

    async def test_done_to_todo(self, db) -> None:
        project = await create_project(db, "p1")
        tg = await create_task_graph(db, project.id, "Task")
        await update_task_graph_status(db, tg.id, "done")
        updated = await update_task_graph_status(db, tg.id, "todo")
        assert updated is not None
        assert updated.status == "todo"

    async def test_done_to_in_progress(self, db) -> None:
        project = await create_project(db, "p1")
        tg = await create_task_graph(db, project.id, "Task")
        await update_task_graph_status(db, tg.id, "done")
        updated = await update_task_graph_status(db, tg.id, "in_progress")
        assert updated is not None
        assert updated.status == "in_progress"

    async def test_invalid_status(self, db) -> None:
        project = await create_project(db, "p1")
        tg = await create_task_graph(db, project.id, "Task")
        with pytest.raises(ValueError, match="Invalid status"):
            await update_task_graph_status(db, tg.id, "invalid")

    async def test_same_status_rejected(self, db) -> None:
        project = await create_project(db, "p1")
        tg = await create_task_graph(db, project.id, "Task")
        with pytest.raises(ValueError, match="Cannot transition"):
            await update_task_graph_status(db, tg.id, "todo")

    async def test_nonexistent_task_graph(self, db) -> None:
        result = await update_task_graph_status(db, "nope", "done")
        assert result is None

    async def test_dependencies_preserved_after_json_roundtrip(self, db) -> None:
        project = await create_project(db, "p1")
        deps = ["id-1", "id-2", "id-3"]
        tg = await create_task_graph(db, project.id, "Task", dependencies=deps)
        fetched = await get_task_graph(db, tg.id)
        assert fetched is not None
        assert fetched.dependencies == deps

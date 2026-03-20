"""Tests for project delete and archive functionality."""

from __future__ import annotations

import json

import pytest

from atc.state.db import (
    _SCHEMA_SQL,
    archive_project,
    create_context_entry,
    create_leader,
    create_project,
    create_session,
    create_task_graph,
    delete_project,
    get_connection,
    get_project,
    list_projects,
    run_migrations,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db():
    """In-memory database with schema applied."""
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.fixture
async def project(db):
    return await create_project(db, "test-project", description="A test project")


# ---------------------------------------------------------------------------
# delete_project tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDeleteProject:
    async def test_delete_removes_project(self, db, project) -> None:
        result = await delete_project(db, project.id)
        assert result is True
        assert await get_project(db, project.id) is None

    async def test_delete_nonexistent_returns_false(self, db) -> None:
        result = await delete_project(db, "nonexistent-id")
        assert result is False

    async def test_delete_cascades_leader(self, db, project) -> None:
        await create_leader(db, project.id)
        result = await delete_project(db, project.id)
        assert result is True
        assert await get_project(db, project.id) is None

    async def test_delete_cascades_sessions(self, db, project) -> None:
        await create_session(db, project.id, "ace", "ace-1")
        result = await delete_project(db, project.id)
        assert result is True

    async def test_delete_cascades_context_entries(self, db, project) -> None:
        await create_context_entry(
            db,
            "project",
            "key1",
            "text",
            json.dumps("value"),
            project_id=project.id,
        )
        result = await delete_project(db, project.id)
        assert result is True

    async def test_delete_cascades_task_graphs(self, db, project) -> None:
        await create_task_graph(db, project.id, "Test Graph")
        result = await delete_project(db, project.id)
        assert result is True

    async def test_delete_removes_from_list(self, db, project) -> None:
        projects_before = await list_projects(db)
        assert len(projects_before) == 1
        await delete_project(db, project.id)
        projects_after = await list_projects(db)
        assert len(projects_after) == 0


# ---------------------------------------------------------------------------
# archive_project tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestArchiveProject:
    async def test_archive_sets_status(self, db, project) -> None:
        result = await archive_project(db, project.id)
        assert result is True
        updated = await get_project(db, project.id)
        assert updated is not None
        assert updated.status == "archived"

    async def test_archive_nonexistent_returns_false(self, db) -> None:
        result = await archive_project(db, "nonexistent-id")
        assert result is False

    async def test_archived_project_still_in_list(self, db, project) -> None:
        await archive_project(db, project.id)
        projects = await list_projects(db)
        assert len(projects) == 1
        assert projects[0].status == "archived"

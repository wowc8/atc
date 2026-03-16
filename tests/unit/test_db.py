"""Unit tests for the database layer."""

from __future__ import annotations

import pytest

from atc.state.db import (
    create_leader,
    create_project,
    create_session,
    delete_session,
    get_connection,
    get_leader_by_project,
    get_project,
    get_session,
    list_active_sessions,
    list_projects,
    list_sessions,
    run_migrations,
    update_session_status,
    update_session_tmux,
)


@pytest.fixture
async def db():
    """Provide an in-memory database with schema applied."""
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        # Re-run migrations on this specific connection
        from atc.state.db import _SCHEMA_SQL

        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.mark.asyncio
class TestProjectCRUD:
    async def test_create_and_get(self, db) -> None:
        project = await create_project(db, "test-project", description="A test")
        assert project.name == "test-project"
        assert project.status == "active"
        assert project.description == "A test"

        fetched = await get_project(db, project.id)
        assert fetched is not None
        assert fetched.id == project.id
        assert fetched.name == "test-project"

    async def test_list_projects(self, db) -> None:
        await create_project(db, "proj-a")
        await create_project(db, "proj-b")
        projects = await list_projects(db)
        assert len(projects) == 2

    async def test_get_nonexistent(self, db) -> None:
        result = await get_project(db, "does-not-exist")
        assert result is None


@pytest.mark.asyncio
class TestLeaderCRUD:
    async def test_create_and_get(self, db) -> None:
        project = await create_project(db, "p1")
        leader = await create_leader(db, project.id, goal="Build everything")
        assert leader.project_id == project.id
        assert leader.status == "idle"
        assert leader.goal == "Build everything"

        fetched = await get_leader_by_project(db, project.id)
        assert fetched is not None
        assert fetched.id == leader.id


@pytest.mark.asyncio
class TestSessionCRUD:
    async def test_create_session_db_first(self, db) -> None:
        project = await create_project(db, "p1")
        session = await create_session(db, project.id, "ace", "ace-1", status="connecting")
        assert session.status == "connecting"
        assert session.session_type == "ace"
        assert session.tmux_pane is None

    async def test_update_status(self, db) -> None:
        project = await create_project(db, "p1")
        session = await create_session(db, project.id, "ace", "ace-1")
        await update_session_status(db, session.id, "working")
        updated = await get_session(db, session.id)
        assert updated is not None
        assert updated.status == "working"

    async def test_update_tmux(self, db) -> None:
        project = await create_project(db, "p1")
        session = await create_session(db, project.id, "ace", "ace-1")
        await update_session_tmux(db, session.id, "atc", "%3")
        updated = await get_session(db, session.id)
        assert updated is not None
        assert updated.tmux_session == "atc"
        assert updated.tmux_pane == "%3"

    async def test_delete_session(self, db) -> None:
        project = await create_project(db, "p1")
        session = await create_session(db, project.id, "ace", "ace-1")
        await delete_session(db, session.id)
        assert await get_session(db, session.id) is None

    async def test_list_sessions_filter(self, db) -> None:
        project = await create_project(db, "p1")
        await create_session(db, project.id, "ace", "ace-1")
        await create_session(db, project.id, "manager", "leader-1")
        await create_session(db, project.id, "ace", "ace-2")

        aces = await list_sessions(db, project_id=project.id, session_type="ace")
        assert len(aces) == 2
        assert all(s.session_type == "ace" for s in aces)

        managers = await list_sessions(db, session_type="manager")
        assert len(managers) == 1

    async def test_list_active_sessions(self, db) -> None:
        project = await create_project(db, "p1")
        s1 = await create_session(db, project.id, "ace", "ace-1", status="idle")
        await update_session_tmux(db, s1.id, "atc", "%1")

        s2 = await create_session(db, project.id, "ace", "ace-2", status="error")
        # error sessions excluded from active list

        active = await list_active_sessions(db)
        ids = [s.id for s in active]
        assert s1.id in ids
        assert s2.id not in ids

    async def test_boolean_conversion(self, db) -> None:
        project = await create_project(db, "p1")
        session = await create_session(db, project.id, "ace", "ace-1")
        fetched = await get_session(db, session.id)
        assert fetched is not None
        assert isinstance(fetched.alternate_on, bool)
        assert isinstance(fetched.auto_accept, bool)
        assert fetched.alternate_on is False

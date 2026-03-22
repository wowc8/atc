"""Tests for context_entries migration, model, CRUD helpers, and inheritance."""

from __future__ import annotations

import json

import pytest

from atc.state.db import (
    _SCHEMA_SQL,
    create_context_entry,
    create_project,
    create_session,
    delete_context_entry,
    get_connection,
    get_context_entry,
    get_context_for_agent,
    list_context_entries_by_project,
    list_context_entries_by_scope,
    run_migrations,
    update_context_entry,
)
from atc.state.migrations import run_migrations as sync_run_migrations
from atc.state.models import ContextEntry


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


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestContextEntryModel:
    def test_defaults(self) -> None:
        entry = ContextEntry(id="e1", key="k", entry_type="text", value="v")
        assert entry.scope == "project"
        assert entry.project_id is None
        assert entry.session_id is None
        assert entry.restricted is False
        assert entry.position == 0
        assert entry.updated_by == ""

    def test_all_fields(self) -> None:
        entry = ContextEntry(
            id="e1",
            key="k",
            entry_type="json",
            value='{"a":1}',
            scope="global",
            project_id="p1",
            session_id="s1",
            restricted=True,
            position=5,
            updated_by="user",
            created_at="2025-01-01",
            updated_at="2025-01-01",
        )
        assert entry.scope == "global"
        assert entry.restricted is True
        assert entry.position == 5


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------


class TestContextEntriesMigration:
    def test_migration_applies_cleanly(self) -> None:
        """The real 008 migration applies on top of previous migrations."""
        from atc.state.db import ConnectionFactory

        factory = ConnectionFactory(":memory:")
        applied = sync_run_migrations(factory)
        assert "008_context_entries_multi_scope.sql" in applied

        # Verify new columns exist
        with factory.connection() as conn:
            cursor = conn.execute("PRAGMA table_info(context_entries)")
            columns = {row[1] for row in cursor.fetchall()}
        assert "scope" in columns
        assert "session_id" in columns
        assert "restricted" in columns

    def test_migration_unique_constraint(self) -> None:
        """New unique constraint is (scope, project_id, session_id, key)."""
        from atc.state.db import ConnectionFactory

        factory = ConnectionFactory(":memory:")
        sync_run_migrations(factory)

        with factory.connection() as conn:
            # Insert first entry
            conn.execute(
                """INSERT INTO context_entries
                   (id, scope, project_id, session_id, key, entry_type, value,
                    created_at, updated_at)
                   VALUES ('e1', 'global', NULL, NULL, 'k1', 'text', '"v"',
                           datetime('now'), datetime('now'))"""
            )
            conn.commit()

            # Same scope+project+session+key should fail
            with pytest.raises(Exception):
                conn.execute(
                    """INSERT INTO context_entries
                       (id, scope, project_id, session_id, key, entry_type, value,
                        created_at, updated_at)
                       VALUES ('e2', 'global', NULL, NULL, 'k1', 'text', '"v2"',
                               datetime('now'), datetime('now'))"""
                )

    def test_migration_preserves_existing_data(self) -> None:
        """Existing rows get scope='project', restricted=1 after migration."""
        from atc.state.db import ConnectionFactory

        factory = ConnectionFactory(":memory:")

        # Apply migrations up to 007 first (via initial schema + incremental)
        # The initial schema creates context_entries with NOT NULL project_id
        with factory.connection() as conn:
            # Create the table with old schema
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    repo_path TEXT,
                    github_repo TEXT,
                    agent_provider TEXT NOT NULL DEFAULT 'claude_code',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    session_type TEXT NOT NULL,
                    task_id TEXT,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'idle',
                    host TEXT,
                    tmux_session TEXT,
                    tmux_pane TEXT,
                    alternate_on INTEGER DEFAULT 0,
                    auto_accept INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS _migrations (
                    version INTEGER PRIMARY KEY,
                    filename TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS context_entries (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    key TEXT NOT NULL,
                    entry_type TEXT NOT NULL,
                    value TEXT NOT NULL,
                    position INTEGER NOT NULL DEFAULT 0,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, key)
                );
                INSERT INTO projects (id, name, created_at, updated_at)
                    VALUES ('p1', 'Test', datetime('now'), datetime('now'));
                INSERT INTO context_entries
                    (id, project_id, key, entry_type, value, position, updated_by,
                     created_at, updated_at)
                    VALUES ('e1', 'p1', 'arch', 'text', '"monorepo"', 0, 'user',
                            datetime('now'), datetime('now'));
                CREATE TABLE IF NOT EXISTS usage_events (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    session_id TEXT,
                    event_type TEXT NOT NULL,
                    model TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    cost_usd REAL,
                    cpu_pct REAL,
                    ram_mb REAL,
                    api_calls INTEGER,
                    recorded_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_budgets (
                    project_id TEXT PRIMARY KEY,
                    daily_token_limit INTEGER,
                    monthly_cost_limit REAL,
                    warn_threshold REAL NOT NULL DEFAULT 0.8,
                    current_status TEXT NOT NULL DEFAULT 'ok',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS github_prs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    number INTEGER NOT NULL,
                    title TEXT,
                    status TEXT,
                    ci_status TEXT,
                    url TEXT,
                    updated_at TEXT NOT NULL
                );
                -- Mark migrations 1-7 as applied so only 008 runs
                INSERT INTO _migrations (version, filename) VALUES (1, '001_initial_schema.sql');
                INSERT INTO _migrations (version, filename) VALUES (2, '002_task_graphs.sql');
                INSERT INTO _migrations (version, filename) VALUES (3, '003_project_agent_provider.sql');
                INSERT INTO _migrations (version, filename) VALUES (5, '005_app_events.sql');
                INSERT INTO _migrations (version, filename) VALUES (6, '006_task_assignments.sql');
                INSERT INTO _migrations (version, filename) VALUES (7, '007_feature_flags.sql');
            """)
            conn.commit()

        # Now apply 008
        applied = sync_run_migrations(factory)
        assert "008_context_entries_multi_scope.sql" in applied

        # Check the existing row was migrated
        with factory.connection() as conn:
            row = conn.execute(
                "SELECT scope, restricted FROM context_entries WHERE id = 'e1'"
            ).fetchone()
        assert row[0] == "project"
        assert row[1] == 1  # restricted=True

    def test_migration_nullable_project_id(self) -> None:
        """After migration, project_id should accept NULL (for global/tower)."""
        from atc.state.db import ConnectionFactory

        factory = ConnectionFactory(":memory:")
        sync_run_migrations(factory)

        with factory.connection() as conn:
            # Insert a global entry with NULL project_id
            conn.execute(
                """INSERT INTO context_entries
                   (id, scope, project_id, session_id, key, entry_type, value,
                    created_at, updated_at)
                   VALUES ('g1', 'global', NULL, NULL, 'standards', 'text', '"v"',
                           datetime('now'), datetime('now'))"""
            )
            conn.commit()
            row = conn.execute(
                "SELECT project_id FROM context_entries WHERE id = 'g1'"
            ).fetchone()
        assert row[0] is None


# ---------------------------------------------------------------------------
# CRUD tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestContextEntryCRUD:
    async def test_create_and_get(self, db) -> None:
        entry = await create_context_entry(
            db, "global", "standards", "text", json.dumps("PEP8"),
        )
        assert entry.scope == "global"
        assert entry.key == "standards"
        assert entry.project_id is None

        fetched = await get_context_entry(db, entry.id)
        assert fetched is not None
        assert fetched.key == "standards"

    async def test_create_with_project(self, db) -> None:
        project = await create_project(db, "proj")
        entry = await create_context_entry(
            db, "project", "arch", "text", json.dumps("Clean"),
            project_id=project.id,
        )
        assert entry.project_id == project.id

    async def test_create_with_session(self, db) -> None:
        project = await create_project(db, "proj")
        session = await create_session(db, project.id, "ace", "ace-1")
        entry = await create_context_entry(
            db, "ace", "notes", "text", json.dumps("WIP"),
            session_id=session.id,
        )
        assert entry.session_id == session.id

    async def test_create_invalid_scope(self, db) -> None:
        with pytest.raises(ValueError, match="Invalid scope"):
            await create_context_entry(db, "invalid", "k", "text", "v")

    async def test_create_restricted(self, db) -> None:
        entry = await create_context_entry(
            db, "global", "internal", "text", json.dumps("secret"),
            restricted=True,
        )
        assert entry.restricted is True

    async def test_get_nonexistent(self, db) -> None:
        result = await get_context_entry(db, "nope")
        assert result is None

    async def test_update_value(self, db) -> None:
        entry = await create_context_entry(
            db, "global", "k", "text", json.dumps("old"),
        )
        updated = await update_context_entry(db, entry.id, value=json.dumps("new"))
        assert updated is not None
        assert json.loads(updated.value) == "new"

    async def test_update_multiple_fields(self, db) -> None:
        entry = await create_context_entry(
            db, "global", "k", "text", json.dumps("v"),
        )
        updated = await update_context_entry(
            db, entry.id,
            value=json.dumps("v2"),
            entry_type="json",
            position=10,
            restricted=True,
            updated_by="admin",
        )
        assert updated is not None
        assert updated.entry_type == "json"
        assert updated.position == 10
        assert updated.restricted is True
        assert updated.updated_by == "admin"

    async def test_update_no_changes(self, db) -> None:
        entry = await create_context_entry(
            db, "global", "k", "text", json.dumps("v"),
        )
        result = await update_context_entry(db, entry.id)
        assert result is not None
        assert result.id == entry.id

    async def test_update_nonexistent(self, db) -> None:
        result = await update_context_entry(db, "nope", value="x")
        assert result is None

    async def test_delete(self, db) -> None:
        entry = await create_context_entry(
            db, "global", "k", "text", json.dumps("v"),
        )
        assert await delete_context_entry(db, entry.id) is True
        assert await get_context_entry(db, entry.id) is None

    async def test_delete_nonexistent(self, db) -> None:
        assert await delete_context_entry(db, "nope") is False

    async def test_list_by_scope(self, db) -> None:
        await create_context_entry(db, "global", "g1", "text", json.dumps("v"))
        await create_context_entry(db, "global", "g2", "text", json.dumps("v"))
        project = await create_project(db, "proj")
        await create_context_entry(
            db, "project", "p1", "text", json.dumps("v"),
            project_id=project.id,
        )

        globals_ = await list_context_entries_by_scope(db, "global")
        assert len(globals_) == 2

        projects = await list_context_entries_by_scope(
            db, "project", project_id=project.id,
        )
        assert len(projects) == 1

    async def test_list_by_scope_with_session(self, db) -> None:
        project = await create_project(db, "proj")
        s1 = await create_session(db, project.id, "ace", "ace-1")
        s2 = await create_session(db, project.id, "ace", "ace-2")

        await create_context_entry(
            db, "ace", "notes-1", "text", json.dumps("v"),
            session_id=s1.id,
        )
        await create_context_entry(
            db, "ace", "notes-2", "text", json.dumps("v"),
            session_id=s2.id,
        )

        entries = await list_context_entries_by_scope(db, "ace", session_id=s1.id)
        assert len(entries) == 1
        assert entries[0].key == "notes-1"

    async def test_list_by_project(self, db) -> None:
        project = await create_project(db, "proj")
        await create_context_entry(
            db, "project", "p1", "text", json.dumps("v"),
            project_id=project.id,
        )
        await create_context_entry(
            db, "project", "p2", "text", json.dumps("v"),
            project_id=project.id,
        )
        await create_context_entry(db, "global", "g1", "text", json.dumps("v"))

        entries = await list_context_entries_by_project(db, project.id)
        assert len(entries) == 2
        assert all(e.project_id == project.id for e in entries)

    async def test_unique_constraint_enforced(self, db) -> None:
        """Same (scope, project_id, session_id, key) should raise when non-NULL."""
        project = await create_project(db, "proj")
        await create_context_entry(
            db, "project", "k1", "text", json.dumps("v"),
            project_id=project.id,
        )
        with pytest.raises(Exception):
            await create_context_entry(
                db, "project", "k1", "text", json.dumps("v2"),
                project_id=project.id,
            )

    async def test_same_key_different_scope(self, db) -> None:
        """Same key in different scopes should be allowed."""
        project = await create_project(db, "proj")
        e1 = await create_context_entry(db, "global", "standards", "text", json.dumps("v1"))
        e2 = await create_context_entry(
            db, "project", "standards", "text", json.dumps("v2"),
            project_id=project.id,
        )
        assert e1.id != e2.id

    async def test_boolean_restricted_conversion(self, db) -> None:
        """restricted field should be converted to Python bool."""
        entry = await create_context_entry(
            db, "global", "k", "text", json.dumps("v"), restricted=True,
        )
        fetched = await get_context_entry(db, entry.id)
        assert fetched is not None
        assert isinstance(fetched.restricted, bool)
        assert fetched.restricted is True

    async def test_position_ordering(self, db) -> None:
        """Entries should be returned ordered by position."""
        await create_context_entry(
            db, "global", "z-last", "text", json.dumps("v"), position=10,
        )
        await create_context_entry(
            db, "global", "a-first", "text", json.dumps("v"), position=0,
        )
        await create_context_entry(
            db, "global", "m-mid", "text", json.dumps("v"), position=5,
        )

        entries = await list_context_entries_by_scope(db, "global")
        assert [e.key for e in entries] == ["a-first", "m-mid", "z-last"]


# ---------------------------------------------------------------------------
# Inheritance / get_context_for_agent tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestContextInheritance:
    async def _setup_entries(self, db):
        """Create a full hierarchy of context entries for testing."""
        project = await create_project(db, "proj")
        tower_session = await create_session(db, project.id, "tower", "tower-1")
        leader_session = await create_session(db, project.id, "manager", "leader-1")
        ace_session = await create_session(db, project.id, "ace", "ace-1")

        global_entry = await create_context_entry(
            db, "global", "coding-standards", "text", json.dumps("PEP8"),
        )
        project_entry = await create_context_entry(
            db, "project", "architecture", "text", json.dumps("Clean"),
            project_id=project.id,
        )
        tower_entry = await create_context_entry(
            db, "tower", "strategy", "text", json.dumps("Scale first"),
            session_id=tower_session.id,
        )
        leader_entry = await create_context_entry(
            db, "leader", "plan", "text", json.dumps("Split into 3 tasks"),
            session_id=leader_session.id,
        )
        ace_entry = await create_context_entry(
            db, "ace", "wip", "text", json.dumps("Implementing auth"),
            session_id=ace_session.id,
        )

        return {
            "project": project,
            "tower_session": tower_session,
            "leader_session": leader_session,
            "ace_session": ace_session,
            "global_entry": global_entry,
            "project_entry": project_entry,
            "tower_entry": tower_entry,
            "leader_entry": leader_entry,
            "ace_entry": ace_entry,
        }

    async def test_ace_sees_global_project_leader_own(self, db) -> None:
        """Ace should see global + project + leader (parent) + own ace entries."""
        data = await self._setup_entries(db)

        entries = await get_context_for_agent(
            db,
            "ace",
            project_id=data["project"].id,
            session_id=data["ace_session"].id,
            parent_session_id=data["leader_session"].id,
        )
        keys = {e.key for e in entries}
        assert "coding-standards" in keys  # global
        assert "architecture" in keys  # project
        assert "plan" in keys  # leader (parent)
        assert "wip" in keys  # own ace

        # Should NOT see tower or other aces
        assert "strategy" not in keys

    async def test_leader_sees_global_project_own(self, db) -> None:
        """Leader should see global + project + own leader entries."""
        data = await self._setup_entries(db)

        entries = await get_context_for_agent(
            db,
            "leader",
            project_id=data["project"].id,
            session_id=data["leader_session"].id,
        )
        keys = {e.key for e in entries}
        assert "coding-standards" in keys  # global
        assert "architecture" in keys  # project
        assert "plan" in keys  # own leader

        # Should NOT see tower, ace
        assert "strategy" not in keys
        assert "wip" not in keys

    async def test_tower_sees_global_own(self, db) -> None:
        """Tower should see global + own tower entries only."""
        data = await self._setup_entries(db)

        entries = await get_context_for_agent(
            db,
            "tower",
            session_id=data["tower_session"].id,
        )
        keys = {e.key for e in entries}
        assert "coding-standards" in keys  # global
        assert "strategy" in keys  # own tower

        # Should NOT see project, leader, ace
        assert "architecture" not in keys
        assert "plan" not in keys
        assert "wip" not in keys

    async def test_ace_without_parent_session(self, db) -> None:
        """Ace with no parent_session_id should not see leader entries."""
        data = await self._setup_entries(db)

        entries = await get_context_for_agent(
            db,
            "ace",
            project_id=data["project"].id,
            session_id=data["ace_session"].id,
            # no parent_session_id
        )
        keys = {e.key for e in entries}
        assert "coding-standards" in keys
        assert "architecture" in keys
        assert "wip" in keys
        assert "plan" not in keys

    async def test_ace_does_not_see_other_ace_entries(self, db) -> None:
        """An ace should not see entries from other ace sessions."""
        data = await self._setup_entries(db)

        # Create a second ace
        ace2 = await create_session(db, data["project"].id, "ace", "ace-2")
        await create_context_entry(
            db, "ace", "other-wip", "text", json.dumps("Other work"),
            session_id=ace2.id,
        )

        entries = await get_context_for_agent(
            db,
            "ace",
            project_id=data["project"].id,
            session_id=data["ace_session"].id,
            parent_session_id=data["leader_session"].id,
        )
        keys = {e.key for e in entries}
        assert "wip" in keys
        assert "other-wip" not in keys

    async def test_leader_does_not_see_other_leader_entries(self, db) -> None:
        """A leader should not see entries from other leader sessions."""
        data = await self._setup_entries(db)

        leader2 = await create_session(db, data["project"].id, "manager", "leader-2")
        await create_context_entry(
            db, "leader", "other-plan", "text", json.dumps("Other plan"),
            session_id=leader2.id,
        )

        entries = await get_context_for_agent(
            db,
            "leader",
            project_id=data["project"].id,
            session_id=data["leader_session"].id,
        )
        keys = {e.key for e in entries}
        assert "plan" in keys
        assert "other-plan" not in keys

    async def test_invalid_agent_scope(self, db) -> None:
        """Invalid scope should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid agent scope"):
            await get_context_for_agent(db, "invalid")

    async def test_tower_without_session_sees_only_global(self, db) -> None:
        """Tower with no session_id sees only global entries."""
        await create_context_entry(
            db, "global", "g1", "text", json.dumps("v"),
        )
        entries = await get_context_for_agent(db, "tower")
        assert len(entries) == 1
        assert entries[0].key == "g1"

    async def test_ace_sees_project_entries_only_for_own_project(self, db) -> None:
        """Ace should only see project entries for its own project."""
        p1 = await create_project(db, "proj-1")
        p2 = await create_project(db, "proj-2")
        ace = await create_session(db, p1.id, "ace", "ace-1")

        await create_context_entry(
            db, "project", "p1-entry", "text", json.dumps("v"),
            project_id=p1.id,
        )
        await create_context_entry(
            db, "project", "p2-entry", "text", json.dumps("v"),
            project_id=p2.id,
        )

        entries = await get_context_for_agent(
            db, "ace",
            project_id=p1.id,
            session_id=ace.id,
        )
        keys = {e.key for e in entries}
        assert "p1-entry" in keys
        assert "p2-entry" not in keys

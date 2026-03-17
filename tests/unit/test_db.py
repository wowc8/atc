"""Tests for connection factory, retry logic, migration runner, and async CRUD."""

from __future__ import annotations

import sqlite3
import tempfile
from typing import TYPE_CHECKING

import pytest

from atc.state.db import ConnectionFactory
from atc.state.migrations import (
    _applied_versions,
    _discover_migrations,
    _ensure_migrations_table,
    run_migrations,
)

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# ConnectionFactory tests
# ---------------------------------------------------------------------------


class TestConnectionFactory:
    def test_connect_returns_connection(self) -> None:
        factory = ConnectionFactory(":memory:")
        conn = factory.connect()
        assert isinstance(conn, sqlite3.Connection)
        conn.close()

    def test_wal_mode_enabled(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        factory = ConnectionFactory(db_path, wal_mode=True)
        conn = factory.connect()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"

    def test_wal_mode_disabled(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        factory = ConnectionFactory(db_path, wal_mode=False)
        conn = factory.connect()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        # Without WAL enabled, default journal mode is "delete"
        assert mode != "wal"

    def test_foreign_keys_enabled(self) -> None:
        factory = ConnectionFactory(":memory:")
        conn = factory.connect()
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.close()
        assert fk == 1

    def test_row_factory_is_row(self) -> None:
        factory = ConnectionFactory(":memory:")
        conn = factory.connect()
        assert conn.row_factory is sqlite3.Row
        conn.close()

    def test_connection_context_manager(self) -> None:
        factory = ConnectionFactory(":memory:")
        with factory.connection() as conn:
            conn.execute("CREATE TABLE t (id TEXT)")
            conn.commit()
            rows = conn.execute("SELECT * FROM t").fetchall()
            assert rows == []

    def test_transaction_commits(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        factory = ConnectionFactory(db_path)
        with factory.connection() as conn:
            conn.execute("CREATE TABLE t (id TEXT)")
            conn.commit()

        with factory.transaction() as conn:
            conn.execute("INSERT INTO t VALUES ('a')")

        with factory.connection() as conn:
            rows = conn.execute("SELECT id FROM t").fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "a"

    def test_transaction_rollback_on_error(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        factory = ConnectionFactory(db_path)
        with factory.connection() as conn:
            conn.execute("CREATE TABLE t (id TEXT)")
            conn.commit()

        with pytest.raises(ValueError, match="boom"), factory.transaction() as conn:
            conn.execute("INSERT INTO t VALUES ('a')")
            raise ValueError("boom")

        with factory.connection() as conn:
            rows = conn.execute("SELECT id FROM t").fetchall()
            assert len(rows) == 0

    def test_db_path_property(self) -> None:
        factory = ConnectionFactory("/tmp/test.db")
        assert factory.db_path == "/tmp/test.db"

    def test_is_memory(self) -> None:
        mem_factory = ConnectionFactory(":memory:")
        assert mem_factory.is_memory is True
        file_factory = ConnectionFactory("/tmp/test.db")
        assert file_factory.is_memory is False

    def test_close_keepalive(self) -> None:
        factory = ConnectionFactory(":memory:")
        assert factory._keepalive is not None
        factory.close()
        assert factory._keepalive is None
        # Closing again is a no-op
        factory.close()


# ---------------------------------------------------------------------------
# Retry logic tests
# ---------------------------------------------------------------------------


class TestWithRetry:
    def test_success_no_retry(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        factory = ConnectionFactory(db_path)
        with factory.connection() as conn:
            conn.execute("CREATE TABLE t (val INTEGER)")
            conn.commit()

        def insert(conn: sqlite3.Connection) -> str:
            conn.execute("INSERT INTO t VALUES (42)")
            return "ok"

        result = factory.with_retry(insert)
        assert result == "ok"

    def test_retry_on_busy(self) -> None:
        call_count = 0

        def flaky_fn(conn: sqlite3.Connection) -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise sqlite3.OperationalError("database is locked")
            return "success"

        factory = ConnectionFactory(":memory:", retry_delay=0.01)
        result = factory.with_retry(flaky_fn)
        assert result == "success"
        assert call_count == 3

    def test_retry_exhausted(self) -> None:
        def always_busy(conn: sqlite3.Connection) -> None:
            raise sqlite3.OperationalError("database is locked")

        factory = ConnectionFactory(":memory:", max_retries=2, retry_delay=0.01)
        with pytest.raises(sqlite3.OperationalError, match="busy after 3 attempts"):
            factory.with_retry(always_busy)

    def test_non_busy_error_not_retried(self) -> None:
        call_count = 0

        def bad_sql(conn: sqlite3.Connection) -> None:
            nonlocal call_count
            call_count += 1
            raise sqlite3.OperationalError("no such table: nope")

        factory = ConnectionFactory(":memory:", retry_delay=0.01)
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            factory.with_retry(bad_sql)
        assert call_count == 1

    def test_custom_retry_params(self) -> None:
        call_count = 0

        def busy(conn: sqlite3.Connection) -> None:
            nonlocal call_count
            call_count += 1
            raise sqlite3.OperationalError("database is busy")

        factory = ConnectionFactory(":memory:", max_retries=10, retry_delay=1.0)
        with pytest.raises(sqlite3.OperationalError):
            factory.with_retry(busy, max_retries=1, retry_delay=0.01)
        # Should only have tried 2 times (1 + 1 retry)
        assert call_count == 2


# ---------------------------------------------------------------------------
# Migration runner tests
# ---------------------------------------------------------------------------


class TestMigrationRunner:
    def test_ensure_migrations_table(self) -> None:
        factory = ConnectionFactory(":memory:")
        _ensure_migrations_table(factory)
        with factory.connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='_migrations'"
            ).fetchall()
        assert len(rows) == 1

    def test_discover_migrations(self, tmp_path: Path) -> None:
        # Create some migration files
        versions_dir = tmp_path / "versions"
        versions_dir.mkdir()
        (versions_dir / "001_initial.sql").write_text("-- first")
        (versions_dir / "002_add_stuff.sql").write_text("-- second")
        (versions_dir / "not_a_migration.txt").write_text("ignore")
        (versions_dir / "abc_bad.sql").write_text("ignore")

        migrations = _discover_migrations(versions_dir)
        assert len(migrations) == 2
        assert migrations[0][0] == 1
        assert migrations[1][0] == 2
        assert migrations[0][1].name == "001_initial.sql"

    def test_run_migrations_applies_all(self, tmp_path: Path) -> None:
        versions_dir = tmp_path / "versions"
        versions_dir.mkdir()
        (versions_dir / "001_create.sql").write_text(
            "CREATE TABLE IF NOT EXISTS t1 (id TEXT PRIMARY KEY);"
        )
        (versions_dir / "002_extend.sql").write_text(
            "CREATE TABLE IF NOT EXISTS t2 (id TEXT PRIMARY KEY);"
        )

        factory = ConnectionFactory(":memory:")
        applied = run_migrations(factory, versions_dir=versions_dir)
        assert applied == ["001_create.sql", "002_extend.sql"]

        # Tables should exist
        with factory.connection() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "t1" in tables
        assert "t2" in tables
        assert "_migrations" in tables

    def test_run_migrations_skips_applied(self, tmp_path: Path) -> None:
        versions_dir = tmp_path / "versions"
        versions_dir.mkdir()
        (versions_dir / "001_first.sql").write_text(
            "CREATE TABLE IF NOT EXISTS t1 (id TEXT);"
        )

        factory = ConnectionFactory(":memory:")
        first_run = run_migrations(factory, versions_dir=versions_dir)
        assert len(first_run) == 1

        # Add a second migration
        (versions_dir / "002_second.sql").write_text(
            "CREATE TABLE IF NOT EXISTS t2 (id TEXT);"
        )

        second_run = run_migrations(factory, versions_dir=versions_dir)
        assert second_run == ["002_second.sql"]

    def test_run_migrations_empty_dir(self, tmp_path: Path) -> None:
        versions_dir = tmp_path / "versions"
        versions_dir.mkdir()

        factory = ConnectionFactory(":memory:")
        applied = run_migrations(factory, versions_dir=versions_dir)
        assert applied == []

    def test_run_migrations_nonexistent_dir(self, tmp_path: Path) -> None:
        versions_dir = tmp_path / "nope"

        factory = ConnectionFactory(":memory:")
        applied = run_migrations(factory, versions_dir=versions_dir)
        assert applied == []

    def test_applied_versions_tracking(self, tmp_path: Path) -> None:
        versions_dir = tmp_path / "versions"
        versions_dir.mkdir()
        (versions_dir / "001_init.sql").write_text(
            "CREATE TABLE IF NOT EXISTS x (id TEXT);"
        )

        factory = ConnectionFactory(":memory:")
        run_migrations(factory, versions_dir=versions_dir)

        versions = _applied_versions(factory)
        assert versions == {1}

    def test_initial_migration_applies_cleanly(self) -> None:
        """Verify the real 001_initial_schema.sql applies without errors."""
        factory = ConnectionFactory(":memory:")
        applied = run_migrations(factory)
        assert "001_initial_schema.sql" in applied

        # Check all expected tables exist
        expected_tables = {
            "projects",
            "leaders",
            "sessions",
            "tasks",
            "task_graphs",
            "project_budgets",
            "usage_events",
            "github_prs",
            "notifications",
            "config",
            "tower_memory",
            "failure_logs",
            "_migrations",
        }
        with factory.connection() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert expected_tables.issubset(tables)

    def test_initial_migration_is_idempotent(self) -> None:
        """Running migrations twice should not fail."""
        factory = ConnectionFactory(":memory:")
        first = run_migrations(factory)
        second = run_migrations(factory)
        assert len(first) >= 1
        assert len(second) == 0


# ---------------------------------------------------------------------------
# Async CRUD tests (session-state-machine)
# ---------------------------------------------------------------------------

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
    run_migrations as async_run_migrations,
    update_session_status,
    update_session_tmux,
)


@pytest.fixture
async def db():
    """Provide an in-memory database with schema applied."""
    await async_run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
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

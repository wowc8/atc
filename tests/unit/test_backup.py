"""Tests for backup export/import service."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO

import aiosqlite
import pytest

from atc.backup.service import (
    BACKUP_VERSION,
    _create_zip,
    _parse_zip,
    export_all,
    export_project,
    import_all,
    import_project,
)
from atc.state.db import _SCHEMA_SQL, _now, _uuid


@pytest.fixture
async def db() -> aiosqlite.Connection:
    """In-memory aiosqlite connection with schema applied."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = aiosqlite.Row
    await conn.executescript(_SCHEMA_SQL)
    await conn.commit()
    yield conn  # type: ignore[misc]
    await conn.close()


async def _seed_project(db: aiosqlite.Connection) -> str:
    """Insert a test project and related data, return project ID."""
    pid = _uuid()
    now = _now()
    await db.execute(
        "INSERT INTO projects (id, name, status, description, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (pid, "Test Project", "active", "A test", now, now),
    )
    # Leader
    lid = _uuid()
    await db.execute(
        "INSERT INTO leaders (id, project_id, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (lid, pid, "idle", now, now),
    )
    # Task graph
    await db.execute(
        "INSERT INTO task_graphs (id, project_id, title, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (_uuid(), pid, "Task 1", "todo", now, now),
    )
    # Context entry
    await db.execute(
        "INSERT INTO context_entries (id, scope, project_id, key, entry_type, value, position, updated_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_uuid(), "project", pid, "intro", "text", '"hello"', 0, "test", now, now),
    )
    await db.commit()
    return pid


class TestCreateAndParseZip:
    def test_roundtrip(self) -> None:
        data = {"backup_version": BACKUP_VERSION, "hello": "world"}
        raw = _create_zip(data)
        parsed = _parse_zip(raw)
        assert parsed["hello"] == "world"
        assert parsed["backup_version"] == BACKUP_VERSION

    def test_parse_missing_backup_json_raises(self) -> None:
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("other.txt", "nope")
        with pytest.raises(ValueError, match="missing backup.json"):
            _parse_zip(buf.getvalue())

    def test_parse_wrong_version_raises(self) -> None:
        data = {"backup_version": 999}
        raw = _create_zip(data)
        with pytest.raises(ValueError, match="Unsupported backup version"):
            _parse_zip(raw)


@pytest.mark.asyncio
class TestExportProject:
    async def test_export_creates_valid_zip(self, db: aiosqlite.Connection) -> None:
        pid = await _seed_project(db)
        raw = await export_project(db, pid)
        data = _parse_zip(raw)
        assert data["backup_type"] == "project"
        assert data["project"]["id"] == pid
        assert data["project"]["name"] == "Test Project"
        assert len(data["leaders"]) == 1
        assert len(data["task_graphs"]) == 1
        assert len(data["context_entries"]) == 1

    async def test_export_nonexistent_raises(self, db: aiosqlite.Connection) -> None:
        with pytest.raises(ValueError, match="not found"):
            await export_project(db, "fake-id")


@pytest.mark.asyncio
class TestExportAll:
    async def test_export_all(self, db: aiosqlite.Connection) -> None:
        await _seed_project(db)
        raw = await export_all(db)
        data = _parse_zip(raw)
        assert data["backup_type"] == "all"
        assert len(data["projects"]) == 1


@pytest.mark.asyncio
class TestImportProject:
    async def test_import_creates_new_project(self, db: aiosqlite.Connection) -> None:
        pid = await _seed_project(db)
        raw = await export_project(db, pid)

        result = await import_project(db, raw)
        new_pid = result["project_id"]
        assert new_pid != pid  # New ID assigned

        # Verify project exists
        cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (new_pid,))
        row = await cursor.fetchone()
        assert row is not None
        assert dict(row)["name"] == "Test Project"

        # Verify related data was imported
        cursor = await db.execute(
            "SELECT * FROM leaders WHERE project_id = ?", (new_pid,)
        )
        assert await cursor.fetchone() is not None

    async def test_import_into_existing_replaces(self, db: aiosqlite.Connection) -> None:
        pid = await _seed_project(db)
        raw = await export_project(db, pid)

        # Create a second project to restore into
        pid2 = await _seed_project(db)

        result = await import_project(db, raw, target_project_id=pid2)
        assert result["project_id"] == pid2
        assert result["auto_backup_path"] is not None

        # Verify data was replaced
        cursor = await db.execute(
            "SELECT name FROM projects WHERE id = ?", (pid2,)
        )
        row = await cursor.fetchone()
        assert dict(row)["name"] == "Test Project"


@pytest.mark.asyncio
class TestImportAll:
    async def test_import_all(self, db: aiosqlite.Connection) -> None:
        pid = await _seed_project(db)
        raw = await export_all(db)

        # Import all replaces
        result = await import_all(db, raw)
        assert len(result["imported_projects"]) == 1
        assert result["imported_projects"][0]["name"] == "Test Project"

    async def test_import_all_wrong_type_raises(self, db: aiosqlite.Connection) -> None:
        pid = await _seed_project(db)
        raw = await export_project(db, pid)
        with pytest.raises(ValueError, match="Expected 'all'"):
            await import_all(db, raw)

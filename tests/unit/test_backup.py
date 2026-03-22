"""Tests for backup export/import service."""

from __future__ import annotations

import json
import tarfile
import zipfile
from io import BytesIO
from typing import TYPE_CHECKING

import aiosqlite
import pytest
import yaml

if TYPE_CHECKING:
    from pathlib import Path

from atc.backup.service import (
    ATCB_FORMAT_VERSION,
    BACKUP_VERSION,
    BackupService,
    _create_zip,
    _parse_zip,
    _strip_secrets,
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
    cols = (
        "id, scope, project_id, key, entry_type, value, position,"
        " updated_by, created_at, updated_at"
    )
    await db.execute(
        f"INSERT INTO context_entries ({cols}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        await _seed_project(db)
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


# ---------------------------------------------------------------------------
# BackupService (.atcb format) tests
# ---------------------------------------------------------------------------


class TestStripSecrets:
    def test_removes_secret_keys(self) -> None:
        cfg = {
            "anthropic_api_key": "sk-xxx",
            "secret_token": "tok",
            "password": "pass",
            "name": "ATC",
            "nested": {"api_key": "val", "host": "localhost"},
        }
        result = _strip_secrets(cfg)
        assert "anthropic_api_key" not in result
        assert "secret_token" not in result
        assert "password" not in result
        assert result["name"] == "ATC"
        assert "api_key" not in result["nested"]
        assert result["nested"]["host"] == "localhost"

    def test_preserves_non_secret_keys(self) -> None:
        cfg = {"host": "localhost", "port": 8420, "enabled": True}
        assert _strip_secrets(cfg) == cfg

    def test_strips_from_list_values(self) -> None:
        data = [{"api_key": "x", "name": "y"}]
        result = _strip_secrets(data)
        assert isinstance(result, list)
        assert "api_key" not in result[0]
        assert result[0]["name"] == "y"


@pytest.mark.asyncio
class TestBackupServiceCreate:
    async def test_create_produces_valid_atcb(self, tmp_path: Path) -> None:
        """create() should write a gzipped tar with manifest.json."""
        # Create a minimal SQLite DB in tmp_path
        db_path = tmp_path / "atc.db"
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT)")
        conn.commit()
        conn.close()

        svc = BackupService(
            db_path=db_path,
            backup_dir=tmp_path / "backups",
        )
        result = await svc.create()

        assert result.path.exists()
        assert result.path.suffix == ".atcb"
        assert result.size_bytes > 0

        # Validate it's a gzipped tar containing manifest.json
        with tarfile.open(str(result.path), "r:gz") as tar:
            names = tar.getnames()
        assert "manifest.json" in names
        assert "atc.db" in names

        # Validate manifest contents
        with tarfile.open(str(result.path), "r:gz") as tar:
            member = tar.getmember("manifest.json")
            f = tar.extractfile(member)
            assert f is not None
            manifest = json.loads(f.read())

        assert manifest["format_version"] == ATCB_FORMAT_VERSION
        assert "created_at" in manifest
        assert "atc_version" in manifest

    async def test_config_secrets_stripped(self, tmp_path: Path) -> None:
        """The archived config.yaml must not contain secret keys."""
        # Write a config.yaml with secrets
        config_path = tmp_path / "config.yaml"
        cfg = {
            "anthropic": {"api_key": "sk-secret-123"},
            "server": {"host": "localhost", "port": 8420},
        }
        with open(config_path, "w") as f:
            yaml.safe_dump(cfg, f)

        db_path = tmp_path / "atc.db"
        db_path.touch()

        svc = BackupService(
            db_path=db_path,
            config_path=config_path,
            backup_dir=tmp_path / "backups",
        )
        result = await svc.create()

        with tarfile.open(str(result.path), "r:gz") as tar:
            member = tar.getmember("config.yaml")
            f = tar.extractfile(member)
            assert f is not None
            archived_cfg = yaml.safe_load(f.read())

        # api_key must have been stripped
        assert "api_key" not in archived_cfg.get("anthropic", {})
        # Non-secret keys preserved
        assert archived_cfg["server"]["host"] == "localhost"


@pytest.mark.asyncio
class TestBackupServiceRestore:
    async def test_restore_rejects_wrong_format_version(self, tmp_path: Path) -> None:
        """restore() must raise ValueError for incompatible format_version."""
        # Build a fake .atcb with wrong format_version
        atcb_path = tmp_path / "bad.atcb"
        with tarfile.open(str(atcb_path), "w:gz") as tar:
            manifest = json.dumps({"format_version": 999, "atc_version": "0.0.0"})
            manifest_bytes = manifest.encode()
            import io

            info = tarfile.TarInfo(name="manifest.json")
            info.size = len(manifest_bytes)
            tar.addfile(info, io.BytesIO(manifest_bytes))

        db_path = tmp_path / "atc.db"
        db_path.touch()

        svc = BackupService(db_path=db_path, backup_dir=tmp_path / "backups")

        with pytest.raises(ValueError, match="Incompatible backup format version"):
            await svc.restore(atcb_path)

    async def test_restore_rejects_missing_manifest(self, tmp_path: Path) -> None:
        """restore() must raise ValueError when manifest.json is absent."""
        atcb_path = tmp_path / "nomanifest.atcb"
        with tarfile.open(str(atcb_path), "w:gz") as tar:
            import io

            data = b"just a file"
            info = tarfile.TarInfo(name="atc.db")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        db_path = tmp_path / "atc.db"
        db_path.touch()

        svc = BackupService(db_path=db_path, backup_dir=tmp_path / "backups")

        with pytest.raises(ValueError, match="missing manifest.json"):
            await svc.restore(atcb_path)


class TestBackupRotation:
    def test_prune_keeps_last_n(self, tmp_path: Path) -> None:
        """_prune_old_backups() should delete the oldest files beyond keep_last_n."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Create 5 fake backup files with distinct mtime
        files = []
        for i in range(5):
            p = backup_dir / f"atc-backup-2026-03-{i + 1:02d}-00.atcb"
            p.write_bytes(b"x")
            import os
            import time

            os.utime(p, (time.time() + i, time.time() + i))
            files.append(p)

        svc = BackupService(
            db_path=tmp_path / "atc.db",
            backup_dir=backup_dir,
            keep_last_n=3,
        )
        svc._prune_old_backups()

        remaining = sorted(backup_dir.glob("atc-backup-*.atcb"))
        assert len(remaining) == 3
        # The 3 newest should remain
        assert files[2] in remaining
        assert files[3] in remaining
        assert files[4] in remaining

    def test_prune_noop_when_under_limit(self, tmp_path: Path) -> None:
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        for i in range(3):
            (backup_dir / f"atc-backup-2026-03-{i + 1:02d}-00.atcb").write_bytes(b"x")

        svc = BackupService(
            db_path=tmp_path / "atc.db",
            backup_dir=backup_dir,
            keep_last_n=7,
        )
        svc._prune_old_backups()
        assert len(list(backup_dir.glob("*.atcb"))) == 3

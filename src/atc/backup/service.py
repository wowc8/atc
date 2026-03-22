"""ATC backup service — full-system .atcb archives + legacy project zip exports.

The .atcb format is a gzipped tar archive containing:
  - atc.db         (SQLite database — everything lives here)
  - config.yaml    (settings, minus secrets)
  - manifest.json  (ATC version, created_at, db_schema_version, embedding_model)

Secrets (API keys in config.local.yaml) are NOT included — user re-enters them
on restore.

The legacy ``export_project`` / ``import_project`` functions are preserved for
backwards-compatibility with existing callers.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import shutil
import sqlite3
import tarfile
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from atc import __version__
from atc.backup.models import BackupResult, RestoreResult

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# .atcb format constants
# ---------------------------------------------------------------------------

ATCB_FORMAT_VERSION = 1
_SECRET_SUBSTRINGS = frozenset(["key", "secret", "token", "password"])

# ---------------------------------------------------------------------------
# Legacy .atc-backup.zip constants (preserved for existing tests / callers)
# ---------------------------------------------------------------------------

BACKUP_VERSION = 1
BACKUP_DIR = Path.home() / "Library" / "Application Support" / "com.atc" / "backups"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# .atcb helpers
# ---------------------------------------------------------------------------


def _strip_secrets(data: Any) -> Any:
    """Recursively strip dict keys whose name contains a secret-related word."""
    if isinstance(data, dict):
        return {
            k: _strip_secrets(v)
            for k, v in data.items()
            if not any(s in k.lower() for s in _SECRET_SUBSTRINGS)
        }
    if isinstance(data, list):
        return [_strip_secrets(item) for item in data]
    return data


def _checkpoint_wal(db_path: str) -> None:
    """Run WAL checkpoint (sync) before copying the database file."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
    finally:
        conn.close()


def _count_table(db_path: str, table: str) -> int:
    """Return the row count for *table* in the given on-disk database."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# BackupService
# ---------------------------------------------------------------------------


class BackupService:
    """Creates, restores, and auto-rotates full-system .atcb backups."""

    def __init__(
        self,
        db_path: Path,
        config_path: Path | None = None,
        *,
        backup_dir: Path | None = None,
        keep_last_n: int = 7,
    ) -> None:
        self._db_path = db_path
        self._config_path = config_path or Path("config.yaml")
        self._backup_dir = backup_dir or (Path.home() / "Documents" / "ATC Backups")
        self._keep_last_n = keep_last_n
        self._auto_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------

    async def create(self, output_path: Path | None = None) -> BackupResult:
        """Create a .atcb backup archive.

        If *output_path* is None, writes to the configured backup_dir with a
        timestamp-based filename.
        """
        if output_path is None:
            ts = datetime.now(UTC).strftime("%Y-%m-%d-%H")
            await asyncio.to_thread(
                lambda: self._backup_dir.mkdir(parents=True, exist_ok=True)
            )
            output_path = self._backup_dir / f"atc-backup-{ts}.atcb"

        if output_path.suffix != ".atcb":
            output_path = output_path.with_suffix(".atcb")

        created_at = _now()
        db_path_str = str(self._db_path)
        config_path = self._config_path
        version = __version__

        def _do_create() -> tuple[int, dict[str, int]]:
            # 1. Checkpoint WAL
            if Path(db_path_str).exists():
                _checkpoint_wal(db_path_str)

            entry_counts: dict[str, int] = {}

            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)

                # 2. Copy atc.db
                if Path(db_path_str).exists():
                    shutil.copy2(db_path_str, tmp / "atc.db")
                    for table in ("projects", "sessions", "tower_memory"):
                        try:
                            entry_counts[table] = _count_table(
                                str(tmp / "atc.db"), table
                            )
                        except Exception:  # table may not exist yet
                            entry_counts[table] = 0

                # 3. Copy config.yaml (strip secrets)
                if config_path.exists():
                    with open(config_path) as f:
                        cfg: Any = yaml.safe_load(f) or {}
                    stripped = _strip_secrets(cfg)
                    with open(tmp / "config.yaml", "w") as f:
                        yaml.safe_dump(stripped, f, default_flow_style=False)

                # 4. Write manifest.json
                manifest: dict[str, Any] = {
                    "atc_version": version,
                    "format_version": ATCB_FORMAT_VERSION,
                    "created_at": created_at,
                    "db_schema_version": 12,
                    "embedding_model": "none",
                    "entry_counts": entry_counts,
                }
                with open(tmp / "manifest.json", "w") as f:
                    json.dump(manifest, f, indent=2)

                # 5. Create .atcb (gzipped tar)
                assert output_path is not None
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with tarfile.open(str(output_path), "w:gz") as tar:
                    for name in ("atc.db", "config.yaml", "manifest.json"):
                        fp = tmp / name
                        if fp.exists():
                            tar.add(str(fp), arcname=name)

            size = int(output_path.stat().st_size) if output_path.exists() else 0
            return size, entry_counts

        size_bytes, entry_counts = await asyncio.to_thread(_do_create)

        logger.info(
            "Backup created: %s (%d bytes, projects=%d)",
            output_path,
            size_bytes,
            entry_counts.get("projects", 0),
        )

        return BackupResult(
            path=output_path,
            size_bytes=size_bytes,
            created_at=created_at,
            entry_counts=entry_counts,
        )

    # ------------------------------------------------------------------
    # restore
    # ------------------------------------------------------------------

    async def restore(self, backup_path: Path) -> RestoreResult:
        """Restore from a .atcb backup archive.

        Steps:
          1. Validate format version in manifest.json
          2. Backup current atc.db to atc.db.pre-restore
          3. Extract and replace atc.db
          4. Run migrations (idempotent)
          5. Rebuild embeddings via LongTermMemory
        """
        from atc.memory.ltm import LongTermMemory
        from atc.state.db import run_migrations

        db_path = self._db_path

        def _do_restore() -> dict[str, int]:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)

                # Extract archive
                with tarfile.open(str(backup_path), "r:gz") as tar:
                    # Safe extraction: only regular files, no path traversal
                    for member in tar.getmembers():
                        if not member.isreg():
                            continue
                        member_path = Path(member.name)
                        if member_path.is_absolute() or ".." in member_path.parts:
                            raise ValueError(
                                f"Unsafe path in archive: {member.name}"
                            )
                    tar.extractall(tmp)  # noqa: S202

                # Validate manifest
                manifest_path = tmp / "manifest.json"
                if not manifest_path.exists():
                    raise ValueError("Invalid .atcb: missing manifest.json")

                with open(manifest_path) as f:
                    manifest: dict[str, Any] = json.load(f)

                if manifest.get("format_version", 0) != ATCB_FORMAT_VERSION:
                    raise ValueError(
                        f"Incompatible backup format version: "
                        f"{manifest.get('format_version')!r}"
                    )

                # Safety backup of current DB
                if db_path.exists():
                    pre_restore = db_path.with_suffix(".db.pre-restore")
                    shutil.copy2(db_path, pre_restore)
                    logger.info("Pre-restore safety copy: %s", pre_restore)

                # Replace atc.db
                db_copy = tmp / "atc.db"
                if db_copy.exists():
                    shutil.copy2(db_copy, db_path)

            # Count restored entries
            counts: dict[str, int] = {}
            if db_path.exists():
                for table in ("projects", "sessions", "tower_memory"):
                    try:
                        counts[table] = _count_table(str(db_path), table)
                    except Exception:
                        counts[table] = 0
            return counts

        entry_counts = await asyncio.to_thread(_do_restore)

        # Run migrations (idempotent — safe to re-run on restored DB)
        await run_migrations(str(db_path))

        # Rebuild embeddings
        rebuilt = 0
        try:
            import aiosqlite as _aio

            async with _aio.connect(str(db_path)) as emb_db:
                emb_db.row_factory = _aio.Row
                rebuilt = await LongTermMemory.rebuild_embeddings(emb_db)
        except Exception as exc:
            logger.warning("Embedding rebuild failed (non-fatal): %s", exc)

        logger.info(
            "Restore complete — projects=%d sessions=%d memories=%d "
            "rebuilt_embeddings=%d",
            entry_counts.get("projects", 0),
            entry_counts.get("sessions", 0),
            entry_counts.get("tower_memory", 0),
            rebuilt,
        )

        return RestoreResult(
            projects_count=entry_counts.get("projects", 0),
            sessions_count=entry_counts.get("sessions", 0),
            memories_count=entry_counts.get("tower_memory", 0),
            rebuilt_embeddings=rebuilt,
        )

    # ------------------------------------------------------------------
    # Auto-backup
    # ------------------------------------------------------------------

    async def schedule_auto_backup(self, interval_hours: int = 24) -> None:
        """Start background auto-backup loop (idempotent — safe to call twice)."""
        if self._auto_task is not None:
            return
        self._auto_task = asyncio.create_task(
            self._auto_backup_loop(interval_hours),
            name="atc-auto-backup",
        )
        logger.info("Auto-backup scheduled every %dh → %s", interval_hours, self._backup_dir)

    async def stop_auto_backup(self) -> None:
        """Cancel the auto-backup background task."""
        if self._auto_task is not None:
            self._auto_task.cancel()
            try:
                await self._auto_task
            except asyncio.CancelledError:
                pass
            self._auto_task = None

    async def _auto_backup_loop(self, interval_hours: int) -> None:
        while True:
            await asyncio.sleep(interval_hours * 3600)
            try:
                result = await self.create()
                logger.info("Auto-backup complete: %s (%d bytes)", result.path, result.size_bytes)
                await asyncio.to_thread(self._prune_old_backups)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Auto-backup failed: %s", exc)

    def _prune_old_backups(self) -> None:
        """Delete oldest .atcb files keeping only the last N."""
        if not self._backup_dir.exists():
            return
        backups = sorted(
            self._backup_dir.glob("atc-backup-*.atcb"),
            key=lambda p: p.stat().st_mtime,
        )
        to_delete = backups[: max(0, len(backups) - self._keep_last_n)]
        for p in to_delete:
            try:
                p.unlink()
                logger.info("Pruned old backup: %s", p)
            except OSError as exc:
                logger.warning("Failed to prune backup %s: %s", p, exc)


# ---------------------------------------------------------------------------
# Legacy export helpers (preserved for existing tests and callers)
# ---------------------------------------------------------------------------


async def _export_table_rows(
    db: aiosqlite.Connection,
    table: str,
    *,
    where: str = "",
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    """Fetch all rows from a table as dicts."""
    query = f"SELECT * FROM {table}"  # noqa: S608
    if where:
        query += f" WHERE {where}"
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def export_project(db: aiosqlite.Connection, project_id: str) -> bytes:
    """Export a single project and all its data into a zip archive (bytes)."""
    cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
    project_row = await cursor.fetchone()
    if project_row is None:
        raise ValueError(f"Project {project_id} not found")

    project = dict(project_row)

    data: dict[str, Any] = {
        "backup_version": BACKUP_VERSION,
        "backup_type": "project",
        "exported_at": _now(),
        "project": project,
    }

    pid = (project_id,)
    data["leaders"] = await _export_table_rows(
        db, "leaders", where="project_id = ?", params=pid
    )
    data["sessions"] = await _export_table_rows(
        db, "sessions", where="project_id = ?", params=pid
    )
    data["tasks"] = await _export_table_rows(
        db, "tasks", where="project_id = ?", params=pid
    )
    data["task_graphs"] = await _export_table_rows(
        db, "task_graphs", where="project_id = ?", params=pid
    )
    data["context_entries"] = await _export_table_rows(
        db, "context_entries", where="project_id = ?", params=pid
    )
    data["project_budgets"] = await _export_table_rows(
        db, "project_budgets", where="project_id = ?", params=pid
    )
    data["notifications"] = await _export_table_rows(
        db, "notifications", where="project_id = ?", params=pid
    )
    data["config"] = await _export_table_rows(db, "config")
    data["tower_memory"] = await _export_table_rows(
        db, "tower_memory", where="project_id = ?", params=pid
    )

    return _create_zip(data)


async def export_all(db: aiosqlite.Connection) -> bytes:
    """Export all projects and global data into a zip archive (bytes)."""
    data: dict[str, Any] = {
        "backup_version": BACKUP_VERSION,
        "backup_type": "all",
        "exported_at": _now(),
    }

    data["projects"] = await _export_table_rows(db, "projects")
    data["leaders"] = await _export_table_rows(db, "leaders")
    data["sessions"] = await _export_table_rows(db, "sessions")
    data["tasks"] = await _export_table_rows(db, "tasks")
    data["task_graphs"] = await _export_table_rows(db, "task_graphs")
    data["context_entries"] = await _export_table_rows(db, "context_entries")
    data["project_budgets"] = await _export_table_rows(db, "project_budgets")
    data["notifications"] = await _export_table_rows(db, "notifications")
    data["config"] = await _export_table_rows(db, "config")
    data["tower_memory"] = await _export_table_rows(db, "tower_memory")

    return _create_zip(data)


def _create_zip(data: dict[str, Any]) -> bytes:
    """Create a zip archive containing backup.json."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("backup.json", json.dumps(data, indent=2))
    return buf.getvalue()


def _parse_zip(raw: bytes) -> dict[str, Any]:
    """Parse a .atc-backup.zip and return the backup data dict."""
    buf = io.BytesIO(raw)
    with zipfile.ZipFile(buf, "r") as zf:
        if "backup.json" not in zf.namelist():
            raise ValueError("Invalid backup: missing backup.json")
        content = zf.read("backup.json")
    data: dict[str, Any] = json.loads(content)
    if data.get("backup_version") != BACKUP_VERSION:
        raise ValueError(
            f"Unsupported backup version: {data.get('backup_version')}"
        )
    return data


async def _auto_backup_project(db: aiosqlite.Connection, project_id: str) -> Path:
    """Create an auto-backup of an existing project before overwriting."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_bytes = await export_project(db, project_id)
    cursor = await db.execute(
        "SELECT name FROM projects WHERE id = ?", (project_id,)
    )
    row = await cursor.fetchone()
    name = row["name"] if row else "unknown"
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = BACKUP_DIR / f"{safe_name}-{ts}.atc-backup.zip"
    path.write_bytes(backup_bytes)
    logger.info("Auto-backup created: %s", path)
    return path


async def _delete_project_data(db: aiosqlite.Connection, project_id: str) -> None:
    """Delete all data for a project (reverse order of FK dependencies)."""
    pid = (project_id,)
    await db.execute("DELETE FROM context_entries WHERE project_id = ?", pid)
    await db.execute("DELETE FROM task_graphs WHERE project_id = ?", pid)
    await db.execute("DELETE FROM notifications WHERE project_id = ?", pid)
    await db.execute("DELETE FROM project_budgets WHERE project_id = ?", pid)
    await db.execute("DELETE FROM tower_memory WHERE project_id = ?", pid)
    await db.execute("DELETE FROM tasks WHERE project_id = ?", pid)
    await db.execute("DELETE FROM sessions WHERE project_id = ?", pid)
    await db.execute("DELETE FROM leaders WHERE project_id = ?", pid)
    await db.execute("DELETE FROM projects WHERE id = ?", pid)
    await db.commit()


async def _insert_rows(
    db: aiosqlite.Connection,
    table: str,
    rows: list[dict[str, Any]],
) -> None:
    """Insert rows into a table from dicts."""
    if not rows:
        return
    columns = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in columns)
    col_names = ", ".join(columns)
    for row in rows:
        values = [row[c] for c in columns]
        await db.execute(
            f"INSERT OR IGNORE INTO {table} ({col_names}) VALUES ({placeholders})",  # noqa: S608
            values,
        )


async def import_project(
    db: aiosqlite.Connection,
    raw: bytes,
    *,
    target_project_id: str | None = None,
) -> dict[str, Any]:
    """Import a project from a zip archive.

    If target_project_id is given AND exists, auto-backup + replace.
    Otherwise, create a new project with a new ID.
    """
    data = _parse_zip(raw)
    if data.get("backup_type") not in ("project", "all"):
        raise ValueError(f"Unexpected backup type: {data.get('backup_type')}")

    if data["backup_type"] == "all":
        projects = data.get("projects", [])
        if not projects:
            raise ValueError("No projects found in backup")
        project_data = projects[0]
    else:
        project_data = data["project"]

    original_id = project_data["id"]
    auto_backup_path: str | None = None

    if target_project_id:
        cursor = await db.execute(
            "SELECT id FROM projects WHERE id = ?", (target_project_id,)
        )
        existing = await cursor.fetchone()
        if existing:
            backup_path = await _auto_backup_project(db, target_project_id)
            auto_backup_path = str(backup_path)
            await _delete_project_data(db, target_project_id)
            new_id = target_project_id
        else:
            new_id = _uuid()
    else:
        new_id = _uuid()

    project_data["id"] = new_id
    now = _now()
    project_data["created_at"] = now
    project_data["updated_at"] = now

    await _insert_rows(db, "projects", [project_data])

    for table in [
        "leaders",
        "sessions",
        "tasks",
        "task_graphs",
        "context_entries",
        "notifications",
        "tower_memory",
    ]:
        rows = data.get(table, [])
        for row in rows:
            if row.get("project_id") == original_id:
                row["project_id"] = new_id
            if "id" in row:
                row["id"] = _uuid()
        await _insert_rows(db, table, rows)

    for row in data.get("project_budgets", []):
        if row.get("project_id") == original_id:
            row["project_id"] = new_id
    await _insert_rows(db, "project_budgets", data.get("project_budgets", []))

    await db.commit()

    return {
        "project_id": new_id,
        "project_name": project_data.get("name", ""),
        "auto_backup_path": auto_backup_path,
    }


async def import_all(
    db: aiosqlite.Connection,
    raw: bytes,
) -> dict[str, Any]:
    """Import all projects from a full backup."""
    data = _parse_zip(raw)
    if data.get("backup_type") != "all":
        raise ValueError(f"Expected 'all' backup type, got: {data.get('backup_type')}")

    existing_projects = await _export_table_rows(db, "projects")
    auto_backup_paths: list[str] = []
    for proj in existing_projects:
        try:
            path = await _auto_backup_project(db, proj["id"])
            auto_backup_paths.append(str(path))
        except Exception:
            logger.exception("Failed to auto-backup project %s", proj["id"])

    for proj in existing_projects:
        await _delete_project_data(db, proj["id"])

    imported_projects: list[dict[str, str]] = []
    backup_projects = data.get("projects", [])
    id_map: dict[str, str] = {}

    for proj in backup_projects:
        old_id = proj["id"]
        new_id = _uuid()
        id_map[old_id] = new_id
        proj["id"] = new_id
        proj["updated_at"] = _now()
        await _insert_rows(db, "projects", [proj])
        imported_projects.append({"id": new_id, "name": proj.get("name", "")})

    for table in [
        "leaders",
        "sessions",
        "tasks",
        "task_graphs",
        "context_entries",
        "notifications",
        "tower_memory",
    ]:
        rows = data.get(table, [])
        for row in rows:
            old_pid = row.get("project_id", "")
            if old_pid in id_map:
                row["project_id"] = id_map[old_pid]
            if "id" in row:
                row["id"] = _uuid()
        await _insert_rows(db, table, rows)

    for row in data.get("project_budgets", []):
        old_pid = row.get("project_id", "")
        if old_pid in id_map:
            row["project_id"] = id_map[old_pid]
    await _insert_rows(db, "project_budgets", data.get("project_budgets", []))

    await _insert_rows(db, "config", data.get("config", []))

    await db.commit()

    return {
        "imported_projects": imported_projects,
        "auto_backup_paths": auto_backup_paths,
    }

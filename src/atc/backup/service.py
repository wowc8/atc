"""Export and import services for ATC project backups.

Exports project data (or all projects) into a .atc-backup.zip archive.
Imports from a zip, always creating new projects (or replacing with auto-backup).
"""

from __future__ import annotations

import io
import json
import logging
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)

BACKUP_VERSION = 1
BACKUP_DIR = Path.home() / "Library" / "Application Support" / "com.atc" / "backups"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------


async def _export_table_rows(
    db: aiosqlite.Connection,
    table: str,
    *,
    where: str = "",
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    """Fetch all rows from a table as dicts."""
    query = f"SELECT * FROM {table}"
    if where:
        query += f" WHERE {where}"
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def export_project(db: aiosqlite.Connection, project_id: str) -> bytes:
    """Export a single project and all its data into a zip archive (bytes)."""
    # Verify project exists
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

    # Export related tables
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

    # All tables, no project filter
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


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------


def _parse_zip(raw: bytes) -> dict[str, Any]:
    """Parse a .atc-backup.zip and return the backup data dict."""
    buf = io.BytesIO(raw)
    with zipfile.ZipFile(buf, "r") as zf:
        if "backup.json" not in zf.namelist():
            raise ValueError("Invalid backup: missing backup.json")
        content = zf.read("backup.json")
    data = json.loads(content)
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
            f"INSERT OR IGNORE INTO {table} ({col_names}) VALUES ({placeholders})",
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

    Returns dict with created/replaced project info.
    """
    data = _parse_zip(raw)
    if data.get("backup_type") not in ("project", "all"):
        raise ValueError(f"Unexpected backup type: {data.get('backup_type')}")

    # For "all" type backups, extract first project
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
        # Check if target project exists
        cursor = await db.execute(
            "SELECT id FROM projects WHERE id = ?", (target_project_id,)
        )
        existing = await cursor.fetchone()
        if existing:
            # Auto-backup before replacing
            backup_path = await _auto_backup_project(db, target_project_id)
            auto_backup_path = str(backup_path)
            await _delete_project_data(db, target_project_id)
            # Reuse the target project ID
            new_id = target_project_id
        else:
            new_id = _uuid()
    else:
        new_id = _uuid()

    # Remap project ID in all data
    project_data["id"] = new_id
    now = _now()
    project_data["created_at"] = now
    project_data["updated_at"] = now

    # Insert project
    await _insert_rows(db, "projects", [project_data])

    # Insert related data, remapping project_id
    for table in [
        "leaders", "sessions", "tasks", "task_graphs",
        "context_entries", "notifications", "tower_memory",
    ]:
        rows = data.get(table, [])
        for row in rows:
            if row.get("project_id") == original_id:
                row["project_id"] = new_id
            # Generate new IDs to avoid collisions
            if "id" in row:
                row["id"] = _uuid()
        await _insert_rows(db, table, rows)

    # Budget uses project_id as PK
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
    """Import all projects from a full backup.

    Auto-backs up all existing projects before replacing.
    Returns info about the import.
    """
    data = _parse_zip(raw)
    if data.get("backup_type") != "all":
        raise ValueError(f"Expected 'all' backup type, got: {data.get('backup_type')}")

    # Auto-backup all existing projects
    existing_projects = await _export_table_rows(db, "projects")
    auto_backup_paths: list[str] = []
    for proj in existing_projects:
        try:
            path = await _auto_backup_project(db, proj["id"])
            auto_backup_paths.append(str(path))
        except Exception:
            logger.exception("Failed to auto-backup project %s", proj["id"])

    # Delete all existing project data
    for proj in existing_projects:
        await _delete_project_data(db, proj["id"])

    # Import all projects with fresh IDs
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

    # Import related data with remapped IDs
    for table in [
        "leaders", "sessions", "tasks", "task_graphs",
        "context_entries", "notifications", "tower_memory",
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

    # Import global config
    await _insert_rows(db, "config", data.get("config", []))

    await db.commit()

    return {
        "imported_projects": imported_projects,
        "auto_backup_paths": auto_backup_paths,
    }

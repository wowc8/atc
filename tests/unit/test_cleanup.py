"""Tests for orphan session + staging dir cleanup (Issue #129)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from atc.state.db import (
    _SCHEMA_SQL,
    create_project,
    create_session,
    get_connection,
    run_migrations,
)
from atc.core.cleanup import run_startup_cleanup


@pytest.fixture
async def db():
    """In-memory database with schema applied."""
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


async def _count_sessions(db, session_type: str | None = None) -> int:
    if session_type:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM sessions WHERE session_type = ?", (session_type,)
        )
    else:
        cursor = await db.execute("SELECT COUNT(*) FROM sessions")
    row = await cursor.fetchone()
    return row[0]


@pytest.mark.asyncio
async def test_deletes_old_ace_sessions_in_terminal_status(db) -> None:
    """Ace sessions older than 7 days in terminal status are deleted."""
    project = await create_project(db, "proj")

    # Old ace session in terminal status
    await db.execute(
        "INSERT INTO sessions (id, project_id, session_type, name, status, created_at, updated_at)"
        " VALUES ('old-ace', ?, 'ace', 'old', 'disconnected',"
        " datetime('now', '-8 days'), datetime('now', '-8 days'))",
        (project.id,),
    )
    # Recent ace session — should be kept
    await create_session(db, project_id=project.id, session_type="ace", name="new", status="disconnected")
    await db.commit()

    totals = await run_startup_cleanup(db)

    assert totals["ace"] == 1
    assert await _count_sessions(db, "ace") == 1  # only the recent one remains


@pytest.mark.asyncio
async def test_keeps_active_ace_sessions(db) -> None:
    """Ace sessions that are recent or not in terminal status are kept."""
    project = await create_project(db, "proj")
    await create_session(db, project_id=project.id, session_type="ace", name="active", status="working")
    await db.commit()

    totals = await run_startup_cleanup(db)

    assert totals["ace"] == 0
    assert await _count_sessions(db, "ace") == 1


@pytest.mark.asyncio
async def test_deletes_orphaned_manager_sessions(db) -> None:
    """Disconnected manager sessions not referenced by any active leader are deleted."""
    project = await create_project(db, "proj")
    sess = await create_session(
        db, project_id=project.id, session_type="manager", name="mgr", status="disconnected"
    )
    await db.commit()

    totals = await run_startup_cleanup(db)

    assert totals["manager"] == 1
    assert await _count_sessions(db, "manager") == 0


@pytest.mark.asyncio
async def test_keeps_excess_tower_sessions_up_to_limit(db) -> None:
    """Only keeps 5 most recent tower sessions per project; deletes the rest."""
    project = await create_project(db, "proj")

    for i in range(8):
        await db.execute(
            "INSERT INTO sessions (id, project_id, session_type, name, status, created_at, updated_at)"
            " VALUES (?, ?, 'tower', 'tower', 'disconnected',"
            " datetime('now', ? || ' seconds'), datetime('now'))",
            (f"tower-{i}", project.id, f"-{100 - i}"),
        )
    await db.commit()

    totals = await run_startup_cleanup(db)

    assert totals["tower"] == 3
    assert await _count_sessions(db, "tower") == 5


@pytest.mark.asyncio
async def test_removes_staging_dir_for_deleted_sessions(db, tmp_path: Path) -> None:
    """Staging dirs are removed when their session is deleted."""
    project = await create_project(db, "proj")

    # Old ace session with a staging dir
    staging = tmp_path / "old-ace"
    staging.mkdir()
    (staging / "CLAUDE.md").write_text("# test\n")

    await db.execute(
        "INSERT INTO sessions (id, project_id, session_type, name, status, created_at, updated_at)"
        " VALUES ('old-ace', ?, 'ace', 'old', 'error',"
        " datetime('now', '-8 days'), datetime('now', '-8 days'))",
        (project.id,),
    )
    await db.commit()

    with patch("atc.core.cleanup._STAGING_ROOT", tmp_path):
        await run_startup_cleanup(db)

    assert not staging.exists()


@pytest.mark.asyncio
async def test_cleanup_returns_zero_counts_when_nothing_to_clean(db) -> None:
    project = await create_project(db, "proj")
    await create_session(db, project_id=project.id, session_type="ace", name="a", status="working")
    await db.commit()

    totals = await run_startup_cleanup(db)

    assert totals == {"ace": 0, "manager": 0, "tower": 0}

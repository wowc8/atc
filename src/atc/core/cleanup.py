"""Startup and on-demand cleanup of orphaned sessions and staging directories.

Prevents unbounded accumulation of DB rows and /tmp/atc-agents/ directories
across server restarts and extended E2E test sessions.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)

_STAGING_ROOT = Path("/tmp/atc-agents")

# Statuses that indicate a session is fully done and safe to delete
_TERMINAL_STATUSES = ("disconnected", "error", "completed", "cancelled")

# Number of tower sessions to retain per project
_KEEP_TOWER_SESSIONS = 5

# Age threshold (days) for terminal ace sessions
_ACE_MAX_AGE_DAYS = 7


def _remove_staging_dir(session_id: str) -> None:
    """Remove /tmp/atc-agents/<session_id>/ if it exists."""
    staging = _STAGING_ROOT / session_id
    if staging.exists():
        try:
            shutil.rmtree(staging)
            logger.info("Removed staging dir %s", staging)
        except OSError as exc:
            logger.warning("Failed to remove staging dir %s: %s", staging, exc)


async def run_startup_cleanup(db: "aiosqlite.Connection") -> dict[str, int]:
    """Remove orphaned sessions and staging dirs on startup.

    Steps:
    1. Delete ace sessions older than 7 days in terminal statuses.
    2. Delete manager sessions that are disconnected and not referenced by any
       active leader.
    3. Keep only the 5 most recent tower sessions per project; delete the rest.
    4. For every deleted session, remove /tmp/atc-agents/<session_id>/.

    Returns a summary dict with counts of deleted sessions per category.
    """
    totals: dict[str, int] = {"ace": 0, "manager": 0, "tower": 0}

    # ── Step 1: stale ace sessions ──────────────────────────────────────────
    placeholders = ",".join("?" * len(_TERMINAL_STATUSES))
    cursor = await db.execute(
        f"""SELECT id FROM sessions
            WHERE session_type = 'ace'
              AND status IN ({placeholders})
              AND created_at < datetime('now', '-{_ACE_MAX_AGE_DAYS} days')""",
        _TERMINAL_STATUSES,
    )
    ace_rows = await cursor.fetchall()
    for (session_id,) in ace_rows:
        await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        _remove_staging_dir(session_id)
        totals["ace"] += 1

    # ── Step 2: orphaned manager sessions ──────────────────────────────────
    # A manager session is orphaned when it is disconnected AND no leader row
    # holds a reference to its session_id as an active session.
    cursor = await db.execute(
        """SELECT s.id FROM sessions s
           WHERE s.session_type = 'manager'
             AND s.status = 'disconnected'
             AND NOT EXISTS (
                 SELECT 1 FROM leaders l
                 WHERE l.session_id = s.id
                   AND l.status NOT IN ('idle', 'error')
             )"""
    )
    manager_rows = await cursor.fetchall()
    for (session_id,) in manager_rows:
        await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        _remove_staging_dir(session_id)
        totals["manager"] += 1

    # ── Step 3: excess tower sessions (keep 5 most recent per project) ──────
    cursor = await db.execute("SELECT DISTINCT project_id FROM sessions WHERE session_type = 'tower'")
    project_rows = await cursor.fetchall()
    for (project_id,) in project_rows:
        cursor2 = await db.execute(
            """SELECT id FROM sessions
               WHERE session_type = 'tower' AND project_id = ?
               ORDER BY created_at DESC""",
            (project_id,),
        )
        tower_rows = await cursor2.fetchall()
        for (session_id,) in tower_rows[_KEEP_TOWER_SESSIONS:]:
            await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            _remove_staging_dir(session_id)
            totals["tower"] += 1

    await db.commit()

    logger.info(
        "Startup cleanup complete — deleted ace=%d manager=%d tower=%d sessions",
        totals["ace"],
        totals["manager"],
        totals["tower"],
    )
    return totals

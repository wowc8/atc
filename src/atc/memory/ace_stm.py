"""Ace short-term memory — progress snapshots for live Ace sessions.

Stores one row per session in ``ace_stm``.  Written every N tool calls
via the PostToolUse hook so the Tower can read progress without parsing
terminal output, and so compaction cannot lose more than ~5 tool calls
worth of context.

Usage::

    await AceSTM.write_progress(db, session_id, content, tool_call_count)
    content = await AceSTM.get_progress(db, session_id)
    await AceSTM.clear(db, session_id)
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AceSTM:
    """Ace short-term memory operations."""

    @staticmethod
    async def write_progress(
        db: aiosqlite.Connection,
        session_id: str,
        content: str,
        tool_call_count: int,
    ) -> None:
        """Upsert a progress snapshot for *session_id*.

        Creates a new row or updates the existing one.  Safe to call
        from multiple concurrent hooks — SQLite's upsert is atomic.
        """
        now = _now()
        await db.execute(
            """
            INSERT INTO ace_stm (id, session_id, content, tool_call_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                content         = excluded.content,
                tool_call_count = excluded.tool_call_count,
                updated_at      = excluded.updated_at
            """,
            (str(uuid.uuid4()), session_id, content, tool_call_count, now, now),
        )
        await db.commit()
        logger.debug(
            "STM snapshot written for session %s (tool_call_count=%d)",
            session_id,
            tool_call_count,
        )

    @staticmethod
    async def get_progress(
        db: aiosqlite.Connection,
        session_id: str,
    ) -> str | None:
        """Return the latest progress snapshot content, or *None* if absent."""
        cursor = await db.execute(
            "SELECT content FROM ace_stm WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return str(row["content"]) if row is not None else None

    @staticmethod
    async def clear(
        db: aiosqlite.Connection,
        session_id: str,
    ) -> None:
        """Delete the STM row for *session_id* (called on clean session end)."""
        await db.execute("DELETE FROM ace_stm WHERE session_id = ?", (session_id,))
        await db.commit()
        logger.debug("STM cleared for session %s", session_id)

    @staticmethod
    async def prune_old(
        db: aiosqlite.Connection,
        max_age_hours: int = 24,
    ) -> int:
        """Delete STM rows older than *max_age_hours*.  Returns count deleted."""
        from datetime import timedelta

        cutoff = (datetime.now(UTC) - timedelta(hours=max_age_hours)).isoformat()
        cursor = await db.execute(
            "DELETE FROM ace_stm WHERE updated_at < ?",
            (cutoff,),
        )
        await db.commit()
        deleted = cursor.rowcount or 0
        if deleted:
            logger.info("Pruned %d stale STM entries (older than %dh)", deleted, max_age_hours)
        return deleted

    @staticmethod
    async def list_all(db: aiosqlite.Connection) -> list[dict[str, object]]:
        """Return all current STM rows as dicts (for consolidation)."""
        cursor = await db.execute(
            "SELECT session_id, content, tool_call_count, updated_at FROM ace_stm"
            " ORDER BY updated_at DESC"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

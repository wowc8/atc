"""Project session log — append-only log of decisions and findings per project.

Stored as a ``context_entry`` with scope=project, key="session_log", type=list.
Each entry in the JSON list follows the format::

    {
        "ts":      "<ISO-8601>",
        "by":      "<session_id|tower|user>",
        "type":    "decision|finding|result|error",
        "content": "<text>"
    }

Usage::

    await ProjectLog.append(db, project_id, session_id, "finding", "...", "tower")
    entries = await ProjectLog.get_recent(db, project_id, limit=50)
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)

_VALID_TYPES = frozenset({"decision", "finding", "result", "error"})


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ProjectLog:
    """Project session log operations."""

    @staticmethod
    async def append(
        db: aiosqlite.Connection,
        project_id: str,
        session_id: str,
        entry_type: str,
        content: str,
        updated_by: str,
    ) -> None:
        """Append a log entry to the project session log.

        Creates the context entry if it doesn't exist yet.  The entry is stored
        as a JSON list in ``context_entries`` with scope=project, key=session_log.

        Args:
            project_id:  Target project.
            session_id:  Emitting session (or "tower"/"user").
            entry_type:  decision | finding | result | error
            content:     Human-readable description.
            updated_by:  Author identifier written to context_entries.updated_by.
        """
        if entry_type not in _VALID_TYPES:
            logger.warning("Unknown project log entry_type=%r, using 'finding'", entry_type)
            entry_type = "finding"

        new_entry: dict[str, str] = {
            "ts": _now(),
            "by": session_id,
            "type": entry_type,
            "content": content,
        }

        # Fetch existing context entry for this project's session log
        cursor = await db.execute(
            """SELECT id, value FROM context_entries
               WHERE scope = 'project'
                 AND project_id = ?
                 AND key = 'session_log'
                 AND session_id IS NULL""",
            (project_id,),
        )
        row = await cursor.fetchone()
        now = _now()

        if row is not None:
            try:
                entries: list[Any] = json.loads(row["value"])
                if not isinstance(entries, list):
                    entries = []
            except (json.JSONDecodeError, TypeError):
                entries = []
            entries.append(new_entry)
            await db.execute(
                "UPDATE context_entries SET value = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                (json.dumps(entries), now, updated_by, row["id"]),
            )
        else:
            await db.execute(
                """INSERT INTO context_entries
                   (id, scope, project_id, session_id, key, entry_type, value,
                    restricted, position, updated_by, created_at, updated_at)
                   VALUES (?, 'project', ?, NULL, 'session_log', 'list', ?, 0, 0, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    project_id,
                    json.dumps([new_entry]),
                    updated_by,
                    now,
                    now,
                ),
            )

        await db.commit()
        logger.debug(
            "Project log entry appended: project=%s type=%s by=%s",
            project_id,
            entry_type,
            session_id,
        )

    @staticmethod
    async def get_recent(
        db: aiosqlite.Connection,
        project_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return the most recent *limit* log entries for *project_id*.

        Returns an empty list if no log exists yet.
        """
        cursor = await db.execute(
            """SELECT value FROM context_entries
               WHERE scope = 'project'
                 AND project_id = ?
                 AND key = 'session_log'
                 AND session_id IS NULL""",
            (project_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return []
        try:
            entries: list[Any] = json.loads(row["value"])
            if not isinstance(entries, list):
                return []
        except (json.JSONDecodeError, TypeError):
            return []
        # Return the last *limit* entries (most recent at the end of the list)
        return entries[-limit:]

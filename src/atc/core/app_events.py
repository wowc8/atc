"""Structured app-level event logging.

Records queryable events (session lifecycle, task completion, errors, cost
changes) to the ``app_events`` SQLite table.
"""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite

    from atc.api.ws.hub import WsHub

logger = logging.getLogger(__name__)

_ws_hub: WsHub | None = None


def set_ws_hub(hub: WsHub) -> None:
    """Wire the WebSocket hub for real-time event broadcasting."""
    global _ws_hub  # noqa: PLW0603
    _ws_hub = hub


async def log_event(
    db: aiosqlite.Connection,
    *,
    level: str,
    category: str,
    message: str,
    detail: dict[str, Any] | None = None,
    project_id: str | None = None,
    session_id: str | None = None,
) -> str:
    """Insert an app event and return its id."""
    event_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    await db.execute(
        """INSERT INTO app_events
           (id, level, category, message, detail, project_id, session_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id,
            level,
            category,
            message,
            json.dumps(detail) if detail else None,
            project_id,
            session_id,
            now,
        ),
    )
    await db.commit()

    py_level = getattr(logging, level.upper(), logging.INFO)
    logger.log(py_level, "[%s] %s (id=%s)", category, message, event_id)

    if _ws_hub is not None:
        try:
            await _ws_hub.broadcast(
                "app_events",
                {
                    "new": {
                        "id": event_id,
                        "level": level,
                        "category": category,
                        "message": message,
                        "detail": detail,
                        "project_id": project_id,
                        "session_id": session_id,
                        "created_at": now,
                    },
                },
            )
        except Exception:
            logger.debug("Failed to broadcast app event via WebSocket")

    return event_id


async def list_events(
    db: aiosqlite.Connection,
    *,
    level: str | None = None,
    category: str | None = None,
    project_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Query app events with optional filters."""
    clauses: list[str] = []
    params: list[Any] = []

    if level:
        clauses.append("level = ?")
        params.append(level)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    params.extend([limit, offset])

    cursor = await db.execute(
        f"SELECT * FROM app_events{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params,
    )
    rows = await cursor.fetchall()
    results = []
    for r in rows:
        d = dict(r)
        if d.get("detail"):
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                d["detail"] = json.loads(d["detail"])
        results.append(d)
    return results


async def count_events(
    db: aiosqlite.Connection,
    *,
    level: str | None = None,
    category: str | None = None,
    project_id: str | None = None,
) -> int:
    """Return count of matching events."""
    clauses: list[str] = []
    params: list[Any] = []

    if level:
        clauses.append("level = ?")
        params.append(level)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)

    where = " WHERE " + " AND ".join(clauses) if clauses else ""

    cursor = await db.execute(
        f"SELECT COUNT(*) FROM app_events{where}",
        params,
    )
    row = await cursor.fetchone()
    return row[0] if row else 0


async def export_events_json(
    db: aiosqlite.Connection,
    *,
    level: str | None = None,
    category: str | None = None,
    project_id: str | None = None,
    limit: int = 10000,
) -> list[dict[str, Any]]:
    """Export events as a list of dicts (for zip packaging)."""
    return await list_events(db, level=level, category=category, project_id=project_id, limit=limit)

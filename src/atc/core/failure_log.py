"""Failure logging — records errors, warnings, and state violations to the DB."""

from __future__ import annotations

import contextlib
import json
import logging
import traceback
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)


async def log_failure(
    db: aiosqlite.Connection,
    *,
    level: str,
    category: str,
    message: str,
    context: dict[str, Any] | None = None,
    project_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    exc: Exception | None = None,
) -> str:
    """Insert a failure log entry and return its id.

    Args:
        level: error|warning|info
        category: creation_failure|instruction_swallowed|session_stalled|
                  tui_violation|tunnel_failure|verification_failed|
                  budget_breach|parse_error|state_violation|unexpected
        message: Human-readable description.
        context: Arbitrary JSON-serialisable data.
        project_id: Associated project, if any.
        entity_type: tower|leader|ace|system.
        entity_id: Session or entity id.
        exc: Exception to capture stack trace from.
    """
    log_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    stack = (
        traceback.format_exception(type(exc), exc, exc.__traceback__)
        if exc
        else None
    )

    await db.execute(
        """INSERT INTO failure_logs
           (id, level, category, project_id, entity_type, entity_id,
            message, context, stack_trace, resolved, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
        (
            log_id,
            level,
            category,
            project_id,
            entity_type,
            entity_id,
            message,
            json.dumps(context or {}),
            "\n".join(stack) if stack else None,
            now,
        ),
    )
    await db.commit()

    logger.log(
        getattr(logging, level.upper(), logging.ERROR),
        "[%s] %s: %s (id=%s)",
        category,
        entity_type or "system",
        message,
        log_id,
    )
    return log_id


async def resolve_failure(db: aiosqlite.Connection, log_id: str) -> None:
    """Mark a failure log entry as resolved."""
    await db.execute("UPDATE failure_logs SET resolved = 1 WHERE id = ?", (log_id,))
    await db.commit()


async def list_failures(
    db: aiosqlite.Connection,
    *,
    level: str | None = None,
    category: str | None = None,
    project_id: str | None = None,
    resolved: bool | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Query failure logs with optional filters."""
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
    if resolved is not None:
        clauses.append("resolved = ?")
        params.append(int(resolved))

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit)

    cursor = await db.execute(
        f"SELECT * FROM failure_logs{where} ORDER BY created_at DESC LIMIT ?",
        params,
    )
    rows = await cursor.fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["resolved"] = bool(d.get("resolved", 0))
        if d.get("context"):
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                d["context"] = json.loads(d["context"])
        results.append(d)
    return results

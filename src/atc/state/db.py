"""SQLite WAL connection factory, migration runner, and session CRUD helpers.

Uses aiosqlite for async access.  All public functions accept a ``db_path``
string (filepath or ``:memory:`` for tests).
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiosqlite

from atc.state.models import Leader, Project, Session

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations" / "versions"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------


@asynccontextmanager
async def get_connection(db_path: str) -> AsyncIterator[aiosqlite.Connection]:
    """Yield an aiosqlite connection with WAL mode and foreign keys enabled."""
    db = await aiosqlite.connect(db_path)
    try:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        db.row_factory = aiosqlite.Row
        yield db
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    repo_path   TEXT,
    github_repo TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leaders (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id),
    session_id  TEXT,
    context     TEXT,
    goal        TEXT,
    status      TEXT NOT NULL DEFAULT 'idle',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id),
    session_type    TEXT NOT NULL,
    task_id         TEXT,
    name            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'idle',
    host            TEXT,
    tmux_session    TEXT,
    tmux_pane       TEXT,
    alternate_on    INTEGER DEFAULT 0,
    auto_accept     INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id),
    leader_id       TEXT NOT NULL REFERENCES leaders(id),
    parent_task_id  TEXT,
    title           TEXT NOT NULL,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    priority        INTEGER DEFAULT 0,
    assigned_to     TEXT,
    result          TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS failure_logs (
    id           TEXT PRIMARY KEY,
    level        TEXT NOT NULL,
    category     TEXT NOT NULL,
    project_id   TEXT,
    entity_type  TEXT,
    entity_id    TEXT,
    message      TEXT NOT NULL,
    context      TEXT NOT NULL,
    stack_trace  TEXT,
    resolved     INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_budgets (
    project_id          TEXT PRIMARY KEY REFERENCES projects(id),
    daily_token_limit   INTEGER,
    monthly_cost_limit  REAL,
    warn_threshold      REAL DEFAULT 0.8,
    current_status      TEXT DEFAULT 'ok',
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_events (
    id              TEXT PRIMARY KEY,
    project_id      TEXT,
    session_id      TEXT,
    event_type      TEXT NOT NULL,
    model           TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cost_usd        REAL,
    cpu_pct         REAL,
    ram_mb          REAL,
    disk_mb         REAL,
    api_calls       INTEGER,
    recorded_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id          TEXT PRIMARY KEY,
    project_id  TEXT,
    level       TEXT NOT NULL,
    message     TEXT NOT NULL,
    read        INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tower_memory (
    id          TEXT PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    value       TEXT NOT NULL,
    project_id  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS context_entries (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id),
    key         TEXT NOT NULL,
    entry_type  TEXT NOT NULL,
    value       TEXT NOT NULL,
    position    INTEGER NOT NULL DEFAULT 0,
    updated_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(project_id, key)
);
"""


async def run_migrations(db_path: str) -> None:
    """Create all tables (idempotent via IF NOT EXISTS)."""
    async with get_connection(db_path) as db:
        await db.executescript(_SCHEMA_SQL)
        await db.commit()


# ---------------------------------------------------------------------------
# Project helpers
# ---------------------------------------------------------------------------


async def create_project(
    db: aiosqlite.Connection,
    name: str,
    *,
    description: str | None = None,
    repo_path: str | None = None,
    github_repo: str | None = None,
) -> Project:
    """Insert a new project row and return the dataclass."""
    now = _now()
    project = Project(
        id=_uuid(),
        name=name,
        status="active",
        description=description,
        repo_path=repo_path,
        github_repo=github_repo,
        created_at=now,
        updated_at=now,
    )
    await db.execute(
        """INSERT INTO projects
           (id, name, description, repo_path, github_repo, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            project.id,
            project.name,
            project.description,
            project.repo_path,
            project.github_repo,
            project.status,
            project.created_at,
            project.updated_at,
        ),
    )
    await db.commit()
    return project


async def get_project(db: aiosqlite.Connection, project_id: str) -> Project | None:
    """Fetch a single project by id."""
    cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
    row = await cursor.fetchone()
    if row is None:
        return None
    return Project(**dict(row))


async def list_projects(db: aiosqlite.Connection) -> list[Project]:
    """Return all projects."""
    cursor = await db.execute("SELECT * FROM projects ORDER BY created_at DESC")
    rows = await cursor.fetchall()
    return [Project(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# Leader helpers
# ---------------------------------------------------------------------------


async def create_leader(
    db: aiosqlite.Connection,
    project_id: str,
    *,
    goal: str | None = None,
) -> Leader:
    """Insert a new leader row."""
    now = _now()
    leader = Leader(
        id=_uuid(),
        project_id=project_id,
        status="idle",
        goal=goal,
        created_at=now,
        updated_at=now,
    )
    await db.execute(
        """INSERT INTO leaders
           (id, project_id, session_id, context, goal, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            leader.id,
            leader.project_id,
            leader.session_id,
            json.dumps(leader.context) if leader.context else None,
            leader.goal,
            leader.status,
            leader.created_at,
            leader.updated_at,
        ),
    )
    await db.commit()
    return leader


async def get_leader(db: aiosqlite.Connection, leader_id: str) -> Leader | None:
    cursor = await db.execute("SELECT * FROM leaders WHERE id = ?", (leader_id,))
    row = await cursor.fetchone()
    if row is None:
        return None
    d = dict(row)
    if d.get("context"):
        d["context"] = json.loads(d["context"])
    return Leader(**d)


async def get_leader_by_project(db: aiosqlite.Connection, project_id: str) -> Leader | None:
    cursor = await db.execute("SELECT * FROM leaders WHERE project_id = ?", (project_id,))
    row = await cursor.fetchone()
    if row is None:
        return None
    d = dict(row)
    if d.get("context"):
        d["context"] = json.loads(d["context"])
    return Leader(**d)


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------


async def create_session(
    db: aiosqlite.Connection,
    project_id: str,
    session_type: str,
    name: str,
    *,
    task_id: str | None = None,
    host: str | None = None,
    status: str = "connecting",
) -> Session:
    """DB-first session creation — row written *before* tmux pane spawn."""
    now = _now()
    session = Session(
        id=_uuid(),
        project_id=project_id,
        session_type=session_type,
        name=name,
        status=status,
        task_id=task_id,
        host=host,
        created_at=now,
        updated_at=now,
    )
    await db.execute(
        """INSERT INTO sessions
           (id, project_id, session_type, task_id, name, status, host,
            tmux_session, tmux_pane, alternate_on, auto_accept, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session.id,
            session.project_id,
            session.session_type,
            session.task_id,
            session.name,
            session.status,
            session.host,
            session.tmux_session,
            session.tmux_pane,
            int(session.alternate_on),
            int(session.auto_accept),
            session.created_at,
            session.updated_at,
        ),
    )
    await db.commit()
    return session


async def get_session(db: aiosqlite.Connection, session_id: str) -> Session | None:
    cursor = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_session(row)


async def list_sessions(
    db: aiosqlite.Connection,
    *,
    project_id: str | None = None,
    session_type: str | None = None,
) -> list[Session]:
    """List sessions, optionally filtered."""
    clauses: list[str] = []
    params: list[Any] = []
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    if session_type:
        clauses.append("session_type = ?")
        params.append(session_type)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    cursor = await db.execute(
        f"SELECT * FROM sessions{where} ORDER BY created_at DESC", params
    )
    rows = await cursor.fetchall()
    return [_row_to_session(r) for r in rows]


async def update_session_status(
    db: aiosqlite.Connection,
    session_id: str,
    status: str,
) -> None:
    """Update session status and updated_at timestamp."""
    await db.execute(
        "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now(), session_id),
    )
    await db.commit()


async def update_session_tmux(
    db: aiosqlite.Connection,
    session_id: str,
    tmux_session: str,
    tmux_pane: str,
) -> None:
    """Set tmux identifiers after pane spawn."""
    await db.execute(
        "UPDATE sessions SET tmux_session = ?, tmux_pane = ?, updated_at = ? WHERE id = ?",
        (tmux_session, tmux_pane, _now(), session_id),
    )
    await db.commit()


async def delete_session(db: aiosqlite.Connection, session_id: str) -> None:
    """Hard-delete a session row."""
    await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    await db.commit()


async def list_active_sessions(db: aiosqlite.Connection) -> list[Session]:
    """Sessions that should be reconnected on startup (not error/disconnected with no tmux)."""
    cursor = await db.execute(
        """SELECT * FROM sessions
           WHERE status NOT IN ('error')
             AND tmux_pane IS NOT NULL
           ORDER BY created_at""",
    )
    rows = await cursor.fetchall()
    return [_row_to_session(r) for r in rows]


def _row_to_session(row: aiosqlite.Row) -> Session:
    d = dict(row)
    d["alternate_on"] = bool(d.get("alternate_on", 0))
    d["auto_accept"] = bool(d.get("auto_accept", 0))
    return Session(**d)

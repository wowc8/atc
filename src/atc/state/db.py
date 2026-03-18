"""SQLite WAL connection factory with retry logic and async CRUD helpers.

The synchronous ``ConnectionFactory`` is used by the migration runner.
Async helpers use ``aiosqlite`` for runtime database access.
"""

from __future__ import annotations

import itertools
import json
import logging
import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import sleep
from typing import TYPE_CHECKING, Any

import aiosqlite

from atc.state.models import Leader, Project, Session, SessionHeartbeat, TaskGraph

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Generator

logger = logging.getLogger(__name__)

_memory_counter = itertools.count()

# Default retry settings for SQLITE_BUSY
DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_DELAY = 0.1  # seconds, doubles each retry

MIGRATIONS_DIR = Path(__file__).parent / "migrations" / "versions"


# ---------------------------------------------------------------------------
# Sync connection factory (used by migration runner)
# ---------------------------------------------------------------------------


class ConnectionFactory:
    """Manages SQLite connections with WAL mode and retry logic.

    All database access should go through this factory — never use
    bare ``sqlite3.connect()`` elsewhere.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        wal_mode: bool = True,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
    ) -> None:
        raw = str(db_path)
        # Use shared-cache URI for in-memory databases so multiple connections
        # see the same data (each plain `:memory:` connection is isolated).
        if raw == ":memory:":
            n = next(_memory_counter)
            self._db_path = f"file:memdb{n}?mode=memory&cache=shared"
            self._use_uri = True
        else:
            self._db_path = raw
            self._use_uri = False
        self._wal_mode = wal_mode
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        # For shared-cache in-memory DBs, keep one connection open so the
        # database survives between connect()/close() cycles.
        self._keepalive: sqlite3.Connection | None = None
        if self._use_uri:
            self._keepalive = sqlite3.connect(self._db_path, uri=True)

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def is_memory(self) -> bool:
        """True when backed by an in-memory database."""
        return self._use_uri

    def close(self) -> None:
        """Close the keepalive connection (for in-memory databases)."""
        if self._keepalive is not None:
            self._keepalive.close()
            self._keepalive = None

    def _configure(self, conn: sqlite3.Connection) -> None:
        """Apply connection-level pragmas."""
        conn.execute("PRAGMA foreign_keys = ON")
        # WAL is not meaningful for in-memory databases
        if self._wal_mode and not self._use_uri:
            conn.execute("PRAGMA journal_mode = WAL")

    def connect(self) -> sqlite3.Connection:
        """Open a new connection with WAL mode and foreign keys enabled."""
        conn = sqlite3.connect(self._db_path, uri=self._use_uri)
        conn.row_factory = sqlite3.Row
        self._configure(conn)
        return conn

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager that opens, yields, and closes a connection."""
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager that wraps work in a transaction with retry."""
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def with_retry(
        self,
        fn: Any,
        *args: Any,
        max_retries: int | None = None,
        retry_delay: float | None = None,
    ) -> Any:
        """Execute ``fn(conn, *args)`` with exponential-backoff retry on SQLITE_BUSY.

        Returns whatever ``fn`` returns. The connection is opened/closed per attempt.
        """
        retries = max_retries if max_retries is not None else self._max_retries
        delay = retry_delay if retry_delay is not None else self._retry_delay

        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                with self.transaction() as conn:
                    return fn(conn, *args)
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                last_err = exc
                if attempt < retries:
                    wait = delay * (2**attempt)
                    logger.warning(
                        "DB busy (attempt %d/%d), retrying in %.2fs",
                        attempt + 1,
                        retries + 1,
                        wait,
                    )
                    sleep(wait)

        raise sqlite3.OperationalError(f"Database busy after {retries + 1} attempts") from last_err


# ---------------------------------------------------------------------------
# Async connection factory (runtime)
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


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
# Migrations (async)
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    repo_path       TEXT,
    github_repo     TEXT,
    agent_provider  TEXT NOT NULL DEFAULT 'claude_code',
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
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

CREATE TABLE IF NOT EXISTS task_graphs (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id),
    title           TEXT NOT NULL,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'todo',
    assigned_ace_id TEXT,
    dependencies    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_heartbeats (
    session_id          TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    health              TEXT NOT NULL DEFAULT 'alive',
    last_heartbeat_at   TEXT NOT NULL,
    registered_at       TEXT NOT NULL,
    updated_at          TEXT NOT NULL
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

CREATE TABLE IF NOT EXISTS app_events (
    id          TEXT PRIMARY KEY,
    level       TEXT NOT NULL,
    category    TEXT NOT NULL,
    message     TEXT NOT NULL,
    detail      TEXT,
    project_id  TEXT,
    session_id  TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_app_events_created_at ON app_events(created_at);
CREATE INDEX IF NOT EXISTS idx_app_events_level ON app_events(level);
CREATE INDEX IF NOT EXISTS idx_app_events_category ON app_events(category);
CREATE INDEX IF NOT EXISTS idx_app_events_project_id ON app_events(project_id);
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
    agent_provider: str = "claude_code",
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
        agent_provider=agent_provider,
        created_at=now,
        updated_at=now,
    )
    await db.execute(
        """INSERT INTO projects
           (id, name, description, repo_path, github_repo, agent_provider,
            status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            project.id,
            project.name,
            project.description,
            project.repo_path,
            project.github_repo,
            project.agent_provider,
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


async def update_project_agent_provider(
    db: aiosqlite.Connection,
    project_id: str,
    agent_provider: str,
) -> None:
    """Update the agent provider for a project."""
    now = _now()
    await db.execute(
        "UPDATE projects SET agent_provider = ?, updated_at = ? WHERE id = ?",
        (agent_provider, now, project_id),
    )
    await db.commit()


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
    cursor = await db.execute(f"SELECT * FROM sessions{where} ORDER BY created_at DESC", params)
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


# ---------------------------------------------------------------------------
# TaskGraph CRUD
# ---------------------------------------------------------------------------

_VALID_TASK_GRAPH_STATUSES = {"todo", "in_progress", "done"}

_TASK_GRAPH_TRANSITIONS: dict[str, set[str]] = {
    "todo": {"in_progress", "done"},
    "in_progress": {"todo", "done"},
    "done": {"todo", "in_progress"},
}


def _row_to_task_graph(row: aiosqlite.Row) -> TaskGraph:
    d = dict(row)
    if d.get("dependencies"):
        d["dependencies"] = json.loads(d["dependencies"])
    return TaskGraph(**d)


async def create_task_graph(
    db: aiosqlite.Connection,
    project_id: str,
    title: str,
    *,
    description: str | None = None,
    status: str = "todo",
    assigned_ace_id: str | None = None,
    dependencies: list[str] | None = None,
) -> TaskGraph:
    """Insert a new task_graph row and return the dataclass."""
    if status not in _VALID_TASK_GRAPH_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    now = _now()
    tg = TaskGraph(
        id=_uuid(),
        project_id=project_id,
        title=title,
        status=status,
        description=description,
        assigned_ace_id=assigned_ace_id,
        dependencies=dependencies,
        created_at=now,
        updated_at=now,
    )
    await db.execute(
        """INSERT INTO task_graphs
           (id, project_id, title, description, status, assigned_ace_id,
            dependencies, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            tg.id,
            tg.project_id,
            tg.title,
            tg.description,
            tg.status,
            tg.assigned_ace_id,
            tg.dependencies_json(),
            tg.created_at,
            tg.updated_at,
        ),
    )
    await db.commit()
    return tg


async def get_task_graph(
    db: aiosqlite.Connection,
    task_graph_id: str,
) -> TaskGraph | None:
    """Fetch a single task_graph by id."""
    cursor = await db.execute(
        "SELECT * FROM task_graphs WHERE id = ?",
        (task_graph_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_task_graph(row)


async def list_task_graphs(
    db: aiosqlite.Connection,
    *,
    project_id: str | None = None,
) -> list[TaskGraph]:
    """Return task graphs, optionally filtered by project."""
    if project_id:
        cursor = await db.execute(
            "SELECT * FROM task_graphs WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM task_graphs ORDER BY created_at DESC",
        )
    rows = await cursor.fetchall()
    return [_row_to_task_graph(r) for r in rows]


async def update_task_graph(
    db: aiosqlite.Connection,
    task_graph_id: str,
    *,
    title: str | None = None,
    description: str | None = ...,  # type: ignore[assignment]
    assigned_ace_id: str | None = ...,  # type: ignore[assignment]
    dependencies: list[str] | None = ...,  # type: ignore[assignment]
) -> TaskGraph | None:
    """Update a task_graph's fields (only non-sentinel values)."""
    existing = await get_task_graph(db, task_graph_id)
    if existing is None:
        return None

    sets: list[str] = []
    params: list[Any] = []

    if title is not None:
        sets.append("title = ?")
        params.append(title)
    if description is not ...:
        sets.append("description = ?")
        params.append(description)
    if assigned_ace_id is not ...:
        sets.append("assigned_ace_id = ?")
        params.append(assigned_ace_id)
    if dependencies is not ...:
        sets.append("dependencies = ?")
        params.append(json.dumps(dependencies) if dependencies is not None else None)

    if not sets:
        return existing

    sets.append("updated_at = ?")
    params.append(_now())
    params.append(task_graph_id)

    await db.execute(
        f"UPDATE task_graphs SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    await db.commit()
    return await get_task_graph(db, task_graph_id)


async def update_task_graph_status(
    db: aiosqlite.Connection,
    task_graph_id: str,
    new_status: str,
) -> TaskGraph | None:
    """Transition task_graph status with validation."""
    if new_status not in _VALID_TASK_GRAPH_STATUSES:
        raise ValueError(f"Invalid status: {new_status}")

    existing = await get_task_graph(db, task_graph_id)
    if existing is None:
        return None

    allowed = _TASK_GRAPH_TRANSITIONS.get(existing.status, set())
    if new_status not in allowed:
        raise ValueError(f"Cannot transition from '{existing.status}' to '{new_status}'")

    await db.execute(
        "UPDATE task_graphs SET status = ?, updated_at = ? WHERE id = ?",
        (new_status, _now(), task_graph_id),
    )
    await db.commit()
    return await get_task_graph(db, task_graph_id)


async def delete_task_graph(
    db: aiosqlite.Connection,
    task_graph_id: str,
) -> bool:
    """Hard-delete a task_graph row. Returns True if deleted."""
    cursor = await db.execute(
        "DELETE FROM task_graphs WHERE id = ?",
        (task_graph_id,),
    )
    await db.commit()
    return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Heartbeat CRUD
# ---------------------------------------------------------------------------


async def register_heartbeat(
    db: aiosqlite.Connection,
    session_id: str,
) -> SessionHeartbeat:
    """Register a session for heartbeat tracking (upsert)."""
    now = _now()
    await db.execute(
        """INSERT INTO session_heartbeats
           (session_id, health, last_heartbeat_at, registered_at, updated_at)
           VALUES (?, 'alive', ?, ?, ?)
           ON CONFLICT(session_id) DO UPDATE SET
             health = 'alive',
             last_heartbeat_at = excluded.last_heartbeat_at,
             updated_at = excluded.updated_at""",
        (session_id, now, now, now),
    )
    await db.commit()
    return SessionHeartbeat(
        session_id=session_id,
        health="alive",
        last_heartbeat_at=now,
        registered_at=now,
        updated_at=now,
    )


async def record_heartbeat(
    db: aiosqlite.Connection,
    session_id: str,
) -> bool:
    """Record a heartbeat ping. Returns True if session was registered."""
    now = _now()
    cursor = await db.execute(
        """UPDATE session_heartbeats
           SET last_heartbeat_at = ?, health = 'alive', updated_at = ?
           WHERE session_id = ?""",
        (now, now, session_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def get_heartbeat(
    db: aiosqlite.Connection,
    session_id: str,
) -> SessionHeartbeat | None:
    """Fetch heartbeat record for a session."""
    cursor = await db.execute(
        "SELECT * FROM session_heartbeats WHERE session_id = ?",
        (session_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return SessionHeartbeat(**dict(row))


async def list_heartbeats(
    db: aiosqlite.Connection,
) -> list[SessionHeartbeat]:
    """Return all heartbeat records."""
    cursor = await db.execute(
        "SELECT * FROM session_heartbeats ORDER BY last_heartbeat_at DESC",
    )
    rows = await cursor.fetchall()
    return [SessionHeartbeat(**dict(r)) for r in rows]


async def update_heartbeat_health(
    db: aiosqlite.Connection,
    session_id: str,
    health: str,
) -> None:
    """Update the health status of a heartbeat record."""
    now = _now()
    await db.execute(
        "UPDATE session_heartbeats SET health = ?, updated_at = ? WHERE session_id = ?",
        (health, now, session_id),
    )
    await db.commit()


async def deregister_heartbeat(
    db: aiosqlite.Connection,
    session_id: str,
) -> bool:
    """Remove heartbeat tracking for a session. Returns True if removed."""
    cursor = await db.execute(
        "DELETE FROM session_heartbeats WHERE session_id = ?",
        (session_id,),
    )
    await db.commit()
    return cursor.rowcount > 0

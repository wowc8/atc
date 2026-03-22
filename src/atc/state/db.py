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

import aiosqlite  # type: ignore[import-not-found]

from atc.state.models import (
    ContextEntry,
    FeatureFlag,
    GitHubPR,
    Leader,
    Project,
    ProjectBudget,
    QALoopRun,
    Session,
    SessionHeartbeat,
    TaskAssignment,
    TaskGraph,
    UsageEvent,
)

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
    embedding   BLOB,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ace_stm (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    content         TEXT NOT NULL,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ace_stm_session_id ON ace_stm(session_id);
CREATE INDEX IF NOT EXISTS idx_ace_stm_updated_at ON ace_stm(updated_at);

CREATE TABLE IF NOT EXISTS memory_consolidation_runs (
    id                TEXT PRIMARY KEY,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    entries_processed INTEGER NOT NULL DEFAULT 0,
    entries_written   INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'running'
);

CREATE INDEX IF NOT EXISTS idx_consolidation_runs_started_at
    ON memory_consolidation_runs(started_at);

CREATE VIRTUAL TABLE IF NOT EXISTS tower_memory_fts
    USING fts5(memory_id UNINDEXED, key, value);

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
    scope       TEXT NOT NULL,
    project_id  TEXT REFERENCES projects(id),
    session_id  TEXT REFERENCES sessions(id),
    key         TEXT NOT NULL,
    entry_type  TEXT NOT NULL,
    value       TEXT NOT NULL,
    restricted  BOOLEAN DEFAULT 0,
    position    INTEGER NOT NULL DEFAULT 0,
    updated_by  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_context_entries_unique_key
    ON context_entries(scope, COALESCE(project_id, ''), COALESCE(session_id, ''), key);
CREATE INDEX IF NOT EXISTS idx_context_entries_scope ON context_entries(scope);
CREATE INDEX IF NOT EXISTS idx_context_entries_project_id ON context_entries(project_id);
CREATE INDEX IF NOT EXISTS idx_context_entries_session_id ON context_entries(session_id);

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

CREATE TABLE IF NOT EXISTS feature_flags (
    id          TEXT PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT,
    enabled     INTEGER NOT NULL DEFAULT 0,
    metadata    TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_feature_flags_key ON feature_flags(key);

CREATE TABLE IF NOT EXISTS task_assignments (
    id              TEXT PRIMARY KEY,
    task_graph_id   TEXT NOT NULL REFERENCES task_graphs(id) ON DELETE CASCADE,
    ace_session_id  TEXT NOT NULL,
    assignment_id   TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'assigned',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_assignments_task_graph
    ON task_assignments(task_graph_id);
CREATE INDEX IF NOT EXISTS idx_task_assignments_ace_session
    ON task_assignments(ace_session_id);

CREATE TABLE IF NOT EXISTS github_prs (
    id              TEXT PRIMARY KEY,
    project_id      TEXT REFERENCES projects(id),
    number          INTEGER NOT NULL,
    title           TEXT,
    status          TEXT,
    ci_status       TEXT,
    qa_status       TEXT NOT NULL DEFAULT 'pending',
    url             TEXT,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_github_prs_project_status
    ON github_prs(project_id, status);

CREATE TABLE IF NOT EXISTS qa_loop_runs (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id),
    pr_id           TEXT NOT NULL REFERENCES github_prs(id),
    iteration       INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running',
    failure_count   INTEGER NOT NULL DEFAULT 0,
    test_output     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_qa_loop_runs_pr_id ON qa_loop_runs(pr_id);
CREATE INDEX IF NOT EXISTS idx_qa_loop_runs_project_id ON qa_loop_runs(project_id);

CREATE TABLE IF NOT EXISTS backup_log (
    id           TEXT PRIMARY KEY,
    backup_type  TEXT NOT NULL,
    status       TEXT NOT NULL,
    path         TEXT,
    size_bytes   INTEGER,
    error        TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backup_log_created_at ON backup_log(created_at);
CREATE INDEX IF NOT EXISTS idx_backup_log_status ON backup_log(status);
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
    # Assign position after the last existing project.
    cursor = await db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM projects")
    row = await cursor.fetchone()
    next_position: int = row[0] if row else 0
    project = Project(
        id=_uuid(),
        name=name,
        status="active",
        description=description,
        repo_path=repo_path,
        github_repo=github_repo,
        agent_provider=agent_provider,
        position=next_position,
        created_at=now,
        updated_at=now,
    )
    await db.execute(
        """INSERT INTO projects
           (id, name, description, repo_path, github_repo, agent_provider,
            status, position, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            project.id,
            project.name,
            project.description,
            project.repo_path,
            project.github_repo,
            project.agent_provider,
            project.status,
            project.position,
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
    """Return all projects ordered by position, then created_at."""
    cursor = await db.execute(
        "SELECT * FROM projects ORDER BY position ASC, created_at ASC"
    )
    rows = await cursor.fetchall()
    return [Project(**dict(r)) for r in rows]


async def update_project_positions(
    db: aiosqlite.Connection,
    positions: list[tuple[str, int]],
) -> None:
    """Bulk-update positions for a list of (project_id, position) pairs."""
    now = _now()
    await db.executemany(
        "UPDATE projects SET position = ?, updated_at = ? WHERE id = ?",
        [(pos, now, pid) for pid, pos in positions],
    )
    await db.commit()


async def update_project_status(
    db: aiosqlite.Connection,
    project_id: str,
    status: str,
) -> None:
    """Update the status of a project (active|paused|archived)."""
    now = _now()
    await db.execute(
        "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
        (status, now, project_id),
    )
    await db.commit()


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


async def archive_project(
    db: aiosqlite.Connection,
    project_id: str,
) -> bool:
    """Set a project's status to 'archived'. Returns True if updated."""
    now = _now()
    cursor = await db.execute(
        "UPDATE projects SET status = 'archived', updated_at = ? WHERE id = ?",
        (now, project_id),
    )
    await db.commit()
    return bool(cursor.rowcount > 0)


async def delete_project(
    db: aiosqlite.Connection,
    project_id: str,
) -> bool:
    """Hard-delete a project and all dependent rows. Returns True if deleted."""
    import contextlib

    # Delete in dependency order to avoid FK violations.
    dependent_tables = [
        ("task_assignments", "task_graph_id IN (SELECT id FROM task_graphs WHERE project_id = ?)"),
        ("session_heartbeats", "session_id IN (SELECT id FROM sessions WHERE project_id = ?)"),
        ("context_entries", "project_id = ?"),
        ("task_graphs", "project_id = ?"),
        ("tasks", "project_id = ?"),
        ("sessions", "project_id = ?"),
        ("leaders", "project_id = ?"),
        ("project_budgets", "project_id = ?"),
        ("usage_events", "project_id = ?"),
        ("qa_loop_runs", "project_id = ?"),
        ("github_prs", "project_id = ?"),
        ("notifications", "project_id = ?"),
        ("app_events", "project_id = ?"),
        ("failure_logs", "project_id = ?"),
        ("tower_memory", "project_id = ?"),
    ]
    for table, where in dependent_tables:
        with contextlib.suppress(sqlite3.OperationalError):
            await db.execute(f"DELETE FROM {table} WHERE {where}", (project_id,))  # noqa: S608

    cursor = await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    await db.commit()
    return bool(cursor.rowcount > 0)


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

_VALID_TASK_GRAPH_STATUSES = {"todo", "assigned", "in_progress", "review", "done", "error"}

_TASK_GRAPH_TRANSITIONS: dict[str, set[str]] = {
    "todo": {"assigned"},
    "assigned": {"in_progress", "todo", "error"},
    "in_progress": {"review", "done", "error"},
    "review": {"done", "in_progress", "error"},
    "done": {"todo"},  # re-open for retry
    "error": {"todo"},  # retry from scratch
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
    if description is not ...:  # type: ignore[comparison-overlap]
        sets.append("description = ?")
        params.append(description)
    if assigned_ace_id is not ...:  # type: ignore[comparison-overlap]
        sets.append("assigned_ace_id = ?")
        params.append(assigned_ace_id)
    if dependencies is not ...:  # type: ignore[comparison-overlap]
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
    return bool(cursor.rowcount > 0)


# ---------------------------------------------------------------------------
# TaskAssignment CRUD (idempotent assignments)
# ---------------------------------------------------------------------------

_VALID_ASSIGNMENT_STATUSES = {"assigned", "working", "done", "failed"}

_ASSIGNMENT_TRANSITIONS: dict[str, set[str]] = {
    "assigned": {"working", "failed"},
    "working": {"done", "failed"},
    "done": set(),
    "failed": set(),
}


def _row_to_task_assignment(row: aiosqlite.Row) -> TaskAssignment:
    d = dict(row)
    return TaskAssignment(**d)


async def assign_task(
    db: aiosqlite.Connection,
    task_graph_id: str,
    ace_session_id: str,
    assignment_id: str,
) -> tuple[TaskAssignment, bool]:
    """Idempotently assign an Ace to a task graph entry.

    If an assignment with the same ``assignment_id`` already exists, returns
    the existing record and ``False`` (no-op).  Otherwise creates the
    assignment, transitions the task to ``assigned``, and returns the new
    record with ``True``.

    Raises ``ValueError`` if the task is not in a state that allows
    assignment (i.e. not ``todo``).
    """
    # Check for existing assignment with same idempotency key
    cursor = await db.execute(
        "SELECT * FROM task_assignments WHERE assignment_id = ?",
        (assignment_id,),
    )
    existing_row = await cursor.fetchone()
    if existing_row is not None:
        return _row_to_task_assignment(existing_row), False

    # Validate the task exists and is in assignable state
    task = await get_task_graph(db, task_graph_id)
    if task is None:
        raise ValueError(f"TaskGraph {task_graph_id} not found")

    if task.status != "todo":
        raise ValueError(
            f"Cannot assign task in '{task.status}' state "
            f"(must be 'todo'); task_graph_id={task_graph_id}"
        )

    # Check for an existing active assignment on this task (prevent double-assign)
    cursor = await db.execute(
        "SELECT * FROM task_assignments"
        " WHERE task_graph_id = ? AND status IN ('assigned', 'working')",
        (task_graph_id,),
    )
    active_row = await cursor.fetchone()
    if active_row is not None:
        active = _row_to_task_assignment(active_row)
        if active.ace_session_id == ace_session_id:
            # Same ace re-assigned — idempotent no-op
            return active, False
        raise ValueError(
            f"Task {task_graph_id} already has an active assignment to ace {active.ace_session_id}"
        )

    now = _now()
    assignment = TaskAssignment(
        id=_uuid(),
        task_graph_id=task_graph_id,
        ace_session_id=ace_session_id,
        assignment_id=assignment_id,
        status="assigned",
        created_at=now,
        updated_at=now,
    )

    await db.execute(
        """INSERT INTO task_assignments
           (id, task_graph_id, ace_session_id, assignment_id, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            assignment.id,
            assignment.task_graph_id,
            assignment.ace_session_id,
            assignment.assignment_id,
            assignment.status,
            assignment.created_at,
            assignment.updated_at,
        ),
    )

    # Transition the task_graph to 'assigned'
    await db.execute(
        "UPDATE task_graphs SET status = ?, assigned_ace_id = ?, updated_at = ? WHERE id = ?",
        ("assigned", ace_session_id, now, task_graph_id),
    )

    await db.commit()
    return assignment, True


async def get_task_assignment(
    db: aiosqlite.Connection,
    assignment_id: str,
) -> TaskAssignment | None:
    """Fetch a task assignment by its idempotency key."""
    cursor = await db.execute(
        "SELECT * FROM task_assignments WHERE assignment_id = ?",
        (assignment_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_task_assignment(row)


async def get_task_assignment_by_id(
    db: aiosqlite.Connection,
    record_id: str,
) -> TaskAssignment | None:
    """Fetch a task assignment by its primary key."""
    cursor = await db.execute(
        "SELECT * FROM task_assignments WHERE id = ?",
        (record_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_task_assignment(row)


async def list_task_assignments(
    db: aiosqlite.Connection,
    *,
    task_graph_id: str | None = None,
    ace_session_id: str | None = None,
) -> list[TaskAssignment]:
    """List task assignments, optionally filtered."""
    conditions: list[str] = []
    params: list[str] = []

    if task_graph_id is not None:
        conditions.append("task_graph_id = ?")
        params.append(task_graph_id)
    if ace_session_id is not None:
        conditions.append("ace_session_id = ?")
        params.append(ace_session_id)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    cursor = await db.execute(
        f"SELECT * FROM task_assignments{where} ORDER BY created_at DESC",
        params,
    )
    rows = await cursor.fetchall()
    return [_row_to_task_assignment(r) for r in rows]


async def update_task_assignment_status(
    db: aiosqlite.Connection,
    assignment_id: str,
    new_status: str,
) -> TaskAssignment | None:
    """Transition a task assignment's status with validation."""
    if new_status not in _VALID_ASSIGNMENT_STATUSES:
        raise ValueError(f"Invalid assignment status: {new_status}")

    existing = await get_task_assignment(db, assignment_id)
    if existing is None:
        return None

    allowed = _ASSIGNMENT_TRANSITIONS.get(existing.status, set())
    if new_status not in allowed:
        raise ValueError(f"Cannot transition assignment from '{existing.status}' to '{new_status}'")

    now = _now()
    await db.execute(
        "UPDATE task_assignments SET status = ?, updated_at = ? WHERE assignment_id = ?",
        (new_status, now, assignment_id),
    )
    await db.commit()
    return await get_task_assignment(db, assignment_id)


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
    return bool(cursor.rowcount > 0)


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
    return bool(cursor.rowcount > 0)


# ---------------------------------------------------------------------------
# Feature flag helpers
# ---------------------------------------------------------------------------


def _flag_from_row(row: aiosqlite.Row) -> FeatureFlag:
    """Convert a DB row to a FeatureFlag dataclass."""
    d = dict(row)
    d["enabled"] = bool(d.get("enabled", 0))
    return FeatureFlag(**d)


async def create_feature_flag(
    db: aiosqlite.Connection,
    key: str,
    name: str,
    *,
    description: str | None = None,
    enabled: bool = False,
    metadata: str | None = None,
) -> FeatureFlag:
    """Insert a new feature flag."""
    now = _now()
    flag = FeatureFlag(
        id=_uuid(),
        key=key,
        name=name,
        description=description,
        enabled=enabled,
        metadata=metadata,
        created_at=now,
        updated_at=now,
    )
    await db.execute(
        """INSERT INTO feature_flags
           (id, key, name, description, enabled, metadata, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            flag.id,
            flag.key,
            flag.name,
            flag.description,
            int(flag.enabled),
            flag.metadata,
            flag.created_at,
            flag.updated_at,
        ),
    )
    await db.commit()
    return flag


async def get_feature_flag(db: aiosqlite.Connection, key: str) -> FeatureFlag | None:
    """Fetch a feature flag by its unique key."""
    cursor = await db.execute("SELECT * FROM feature_flags WHERE key = ?", (key,))
    row = await cursor.fetchone()
    if row is None:
        return None
    return _flag_from_row(row)


async def list_feature_flags(db: aiosqlite.Connection) -> list[FeatureFlag]:
    """Return all feature flags ordered by creation time."""
    cursor = await db.execute("SELECT * FROM feature_flags ORDER BY created_at ASC")
    rows = await cursor.fetchall()
    return [_flag_from_row(r) for r in rows]


async def update_feature_flag(
    db: aiosqlite.Connection,
    key: str,
    *,
    enabled: bool | None = None,
    name: str | None = None,
    description: str | None = ...,  # type: ignore[assignment]
    metadata: str | None = ...,  # type: ignore[assignment]
) -> FeatureFlag | None:
    """Update a feature flag. Returns None if not found."""
    existing = await get_feature_flag(db, key)
    if existing is None:
        return None

    sets: list[str] = []
    params: list[Any] = []
    if enabled is not None:
        sets.append("enabled = ?")
        params.append(int(enabled))
    if name is not None:
        sets.append("name = ?")
        params.append(name)
    if description is not ...:  # type: ignore[comparison-overlap]
        sets.append("description = ?")
        params.append(description)
    if metadata is not ...:  # type: ignore[comparison-overlap]
        sets.append("metadata = ?")
        params.append(metadata)

    if not sets:
        return existing

    sets.append("updated_at = ?")
    params.append(_now())
    params.append(key)

    await db.execute(
        f"UPDATE feature_flags SET {', '.join(sets)} WHERE key = ?",
        params,
    )
    await db.commit()
    return await get_feature_flag(db, key)


async def delete_feature_flag(db: aiosqlite.Connection, key: str) -> bool:
    """Delete a feature flag by key. Returns True if deleted."""
    cursor = await db.execute("DELETE FROM feature_flags WHERE key = ?", (key,))
    await db.commit()
    return bool(cursor.rowcount > 0)


async def is_feature_enabled(db: aiosqlite.Connection, key: str) -> bool:
    """Check if a feature flag is enabled. Returns False if flag doesn't exist."""
    flag = await get_feature_flag(db, key)
    return flag.enabled if flag is not None else False


# ---------------------------------------------------------------------------
# ContextEntry CRUD
# ---------------------------------------------------------------------------

_VALID_CONTEXT_SCOPES = {"global", "project", "tower", "leader", "ace"}


def _row_to_context_entry(row: aiosqlite.Row) -> ContextEntry:
    """Convert a DB row to a ContextEntry dataclass."""
    d = dict(row)
    d["restricted"] = bool(d.get("restricted", 0))
    return ContextEntry(**d)


async def create_context_entry(
    db: aiosqlite.Connection,
    scope: str,
    key: str,
    entry_type: str,
    value: str,
    *,
    project_id: str | None = None,
    session_id: str | None = None,
    restricted: bool = False,
    position: int = 0,
    updated_by: str = "",
) -> ContextEntry:
    """Insert a new context entry."""
    if scope not in _VALID_CONTEXT_SCOPES:
        raise ValueError(f"Invalid scope: {scope}")
    now = _now()
    entry = ContextEntry(
        id=_uuid(),
        key=key,
        entry_type=entry_type,
        value=value,
        scope=scope,
        project_id=project_id,
        session_id=session_id,
        restricted=restricted,
        position=position,
        updated_by=updated_by,
        created_at=now,
        updated_at=now,
    )
    await db.execute(
        """INSERT INTO context_entries
           (id, scope, project_id, session_id, key, entry_type, value,
            restricted, position, updated_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            entry.id,
            entry.scope,
            entry.project_id,
            entry.session_id,
            entry.key,
            entry.entry_type,
            entry.value,
            int(entry.restricted),
            entry.position,
            entry.updated_by,
            entry.created_at,
            entry.updated_at,
        ),
    )
    await db.commit()
    return entry


async def get_context_entry(
    db: aiosqlite.Connection,
    entry_id: str,
) -> ContextEntry | None:
    """Fetch a single context entry by id."""
    cursor = await db.execute(
        "SELECT * FROM context_entries WHERE id = ?",
        (entry_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_context_entry(row)


async def list_context_entries_by_scope(
    db: aiosqlite.Connection,
    scope: str,
    *,
    project_id: str | None = None,
    session_id: str | None = None,
) -> list[ContextEntry]:
    """List context entries filtered by scope and optionally project/session."""
    conditions = ["scope = ?"]
    params: list[Any] = [scope]
    if project_id is not None:
        conditions.append("project_id = ?")
        params.append(project_id)
    if session_id is not None:
        conditions.append("session_id = ?")
        params.append(session_id)
    where = " AND ".join(conditions)
    cursor = await db.execute(
        f"SELECT * FROM context_entries WHERE {where} ORDER BY position, created_at",
        params,
    )
    rows = await cursor.fetchall()
    return [_row_to_context_entry(r) for r in rows]


async def list_context_entries_by_project(
    db: aiosqlite.Connection,
    project_id: str,
) -> list[ContextEntry]:
    """List all context entries associated with a project (any scope)."""
    cursor = await db.execute(
        "SELECT * FROM context_entries WHERE project_id = ? ORDER BY position, created_at",
        (project_id,),
    )
    rows = await cursor.fetchall()
    return [_row_to_context_entry(r) for r in rows]


async def update_context_entry(
    db: aiosqlite.Connection,
    entry_id: str,
    *,
    value: str | None = None,
    entry_type: str | None = None,
    position: int | None = None,
    restricted: bool | None = None,
    updated_by: str | None = None,
) -> ContextEntry | None:
    """Update a context entry. Returns None if not found."""
    existing = await get_context_entry(db, entry_id)
    if existing is None:
        return None

    sets: list[str] = []
    params: list[Any] = []
    if value is not None:
        sets.append("value = ?")
        params.append(value)
    if entry_type is not None:
        sets.append("entry_type = ?")
        params.append(entry_type)
    if position is not None:
        sets.append("position = ?")
        params.append(position)
    if restricted is not None:
        sets.append("restricted = ?")
        params.append(int(restricted))
    if updated_by is not None:
        sets.append("updated_by = ?")
        params.append(updated_by)

    if not sets:
        return existing

    sets.append("updated_at = ?")
    params.append(_now())
    params.append(entry_id)

    await db.execute(
        f"UPDATE context_entries SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    await db.commit()
    return await get_context_entry(db, entry_id)


async def delete_context_entry(
    db: aiosqlite.Connection,
    entry_id: str,
) -> bool:
    """Delete a context entry by id. Returns True if deleted."""
    cursor = await db.execute(
        "DELETE FROM context_entries WHERE id = ?",
        (entry_id,),
    )
    await db.commit()
    return bool(cursor.rowcount > 0)


async def get_context_for_agent(
    db: aiosqlite.Connection,
    scope: str,
    *,
    project_id: str | None = None,
    session_id: str | None = None,
    parent_session_id: str | None = None,
) -> list[ContextEntry]:
    """Return context entries visible to an agent based on inheritance rules.

    Inheritance:
      - ace:    global + project + leader (parent_session_id) + own ace entries
      - leader: global + project + own leader entries
      - tower:  global + own tower entries
    """
    conditions: list[str] = []
    params: list[Any] = []

    if scope == "ace":
        # Ace sees: global, project (for its project_id), leader (parent), own ace
        parts = ["scope = 'global'"]
        part_params: list[Any] = []
        if project_id is not None:
            parts.append("(scope = 'project' AND project_id = ?)")
            part_params.append(project_id)
        if parent_session_id is not None:
            parts.append("(scope = 'leader' AND session_id = ?)")
            part_params.append(parent_session_id)
        if session_id is not None:
            parts.append("(scope = 'ace' AND session_id = ?)")
            part_params.append(session_id)
        conditions.append(f"({' OR '.join(parts)})")
        params.extend(part_params)

    elif scope == "leader":
        # Leader sees: global, project (for its project_id), own leader entries
        parts = ["scope = 'global'"]
        part_params = []
        if project_id is not None:
            parts.append("(scope = 'project' AND project_id = ?)")
            part_params.append(project_id)
        if session_id is not None:
            parts.append("(scope = 'leader' AND session_id = ?)")
            part_params.append(session_id)
        conditions.append(f"({' OR '.join(parts)})")
        params.extend(part_params)

    elif scope == "tower":
        # Tower sees: global, own tower entries
        parts = ["scope = 'global'"]
        part_params = []
        if session_id is not None:
            parts.append("(scope = 'tower' AND session_id = ?)")
            part_params.append(session_id)
        conditions.append(f"({' OR '.join(parts)})")
        params.extend(part_params)

    else:
        raise ValueError(f"Invalid agent scope: {scope}")

    where = " AND ".join(conditions) if conditions else "1=1"
    cursor = await db.execute(
        f"SELECT * FROM context_entries WHERE {where} ORDER BY position, created_at",
        params,
    )
    rows = await cursor.fetchall()
    return [_row_to_context_entry(r) for r in rows]


# ---------------------------------------------------------------------------
# UsageEvent helpers
# ---------------------------------------------------------------------------


async def write_usage_event(
    db: aiosqlite.Connection,
    event_type: str,
    *,
    project_id: str | None = None,
    session_id: str | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
    cpu_pct: float | None = None,
    ram_mb: float | None = None,
    disk_mb: float | None = None,
    api_calls: int | None = None,
) -> UsageEvent:
    """Insert a usage_events row and return the dataclass."""
    now = _now()
    event = UsageEvent(
        id=_uuid(),
        event_type=event_type,
        recorded_at=now,
        project_id=project_id,
        session_id=session_id,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        cpu_pct=cpu_pct,
        ram_mb=ram_mb,
        disk_mb=disk_mb,
        api_calls=api_calls,
    )
    await db.execute(
        """INSERT INTO usage_events
           (id, project_id, session_id, event_type, model,
            input_tokens, output_tokens, cost_usd,
            cpu_pct, ram_mb, disk_mb, api_calls, recorded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event.id,
            event.project_id,
            event.session_id,
            event.event_type,
            event.model,
            event.input_tokens,
            event.output_tokens,
            event.cost_usd,
            event.cpu_pct,
            event.ram_mb,
            event.disk_mb,
            event.api_calls,
            event.recorded_at,
        ),
    )
    await db.commit()
    return event


# ---------------------------------------------------------------------------
# ProjectBudget helpers
# ---------------------------------------------------------------------------


async def get_project_budget(
    db: aiosqlite.Connection,
    project_id: str,
) -> ProjectBudget | None:
    """Fetch the budget row for a project, or None if not set."""
    cursor = await db.execute(
        "SELECT * FROM project_budgets WHERE project_id = ?",
        (project_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return ProjectBudget(**dict(row))


async def upsert_project_budget(
    db: aiosqlite.Connection,
    project_id: str,
    *,
    daily_token_limit: int | None = None,
    monthly_cost_limit: float | None = None,
    warn_threshold: float = 0.8,
) -> ProjectBudget:
    """Insert or update a project budget row."""
    now = _now()
    await db.execute(
        """INSERT INTO project_budgets
           (project_id, daily_token_limit, monthly_cost_limit, warn_threshold,
            current_status, updated_at)
           VALUES (?, ?, ?, ?, 'ok', ?)
           ON CONFLICT(project_id) DO UPDATE SET
             daily_token_limit  = excluded.daily_token_limit,
             monthly_cost_limit = excluded.monthly_cost_limit,
             warn_threshold     = excluded.warn_threshold,
             updated_at         = excluded.updated_at""",
        (project_id, daily_token_limit, monthly_cost_limit, warn_threshold, now),
    )
    await db.commit()
    budget = await get_project_budget(db, project_id)
    assert budget is not None  # noqa: S101 — we just upserted it
    return budget


async def update_project_budget_status(
    db: aiosqlite.Connection,
    project_id: str,
    status: str,
) -> None:
    """Update only the current_status of a project budget."""
    now = _now()
    await db.execute(
        "UPDATE project_budgets SET current_status = ?, updated_at = ? WHERE project_id = ?",
        (status, now, project_id),
    )
    await db.commit()


async def list_project_budgets(db: aiosqlite.Connection) -> list[ProjectBudget]:
    """Return all project budget rows."""
    cursor = await db.execute("SELECT * FROM project_budgets")
    rows = await cursor.fetchall()
    return [ProjectBudget(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# GitHubPR helpers
# ---------------------------------------------------------------------------


def _row_to_github_pr(row: aiosqlite.Row) -> GitHubPR:
    return GitHubPR(**dict(row))


async def list_github_prs(
    db: aiosqlite.Connection,
    project_id: str,
    *,
    status: str | None = None,
) -> list[GitHubPR]:
    """Return GitHub PRs for a project, optionally filtered by status."""
    if status:
        cursor = await db.execute(
            "SELECT * FROM github_prs WHERE project_id = ? AND status = ? ORDER BY number DESC",
            (project_id, status),
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM github_prs WHERE project_id = ? ORDER BY number DESC",
            (project_id,),
        )
    rows = await cursor.fetchall()
    return [_row_to_github_pr(r) for r in rows]


async def upsert_github_pr(
    db: aiosqlite.Connection,
    pr_id: str,
    project_id: str,
    number: int,
    *,
    title: str | None = None,
    status: str | None = None,
    ci_status: str | None = None,
    url: str | None = None,
) -> GitHubPR:
    """Upsert a GitHub PR row."""
    now = _now()
    await db.execute(
        """INSERT INTO github_prs
           (id, project_id, number, title, status, ci_status, url, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             title     = excluded.title,
             status    = excluded.status,
             ci_status = excluded.ci_status,
             url       = excluded.url,
             updated_at = excluded.updated_at""",
        (pr_id, project_id, number, title, status, ci_status, url, now),
    )
    await db.commit()
    cursor = await db.execute("SELECT * FROM github_prs WHERE id = ?", (pr_id,))
    row = await cursor.fetchone()
    assert row is not None  # noqa: S101
    return _row_to_github_pr(row)


async def get_prs_needing_qa(
    db: aiosqlite.Connection,
    *,
    project_id: str | None = None,
) -> list[GitHubPR]:
    """Return PRs with qa_status in ('pending', 'needs_rerun')."""
    if project_id is not None:
        cursor = await db.execute(
            "SELECT * FROM github_prs"
            " WHERE qa_status IN ('pending', 'needs_rerun') AND project_id = ?"
            " ORDER BY number DESC",
            (project_id,),
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM github_prs"
            " WHERE qa_status IN ('pending', 'needs_rerun')"
            " ORDER BY number DESC",
        )
    rows = await cursor.fetchall()
    return [_row_to_github_pr(r) for r in rows]


async def update_pr_qa_status(
    db: aiosqlite.Connection,
    pr_id: str,
    qa_status: str,
) -> None:
    """Update the qa_status of a github_prs row."""
    await db.execute(
        "UPDATE github_prs SET qa_status = ?, updated_at = ? WHERE id = ?",
        (qa_status, _now(), pr_id),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# QALoopRun helpers
# ---------------------------------------------------------------------------


def _row_to_qa_loop_run(row: aiosqlite.Row) -> QALoopRun:
    return QALoopRun(**dict(row))


async def create_qa_loop_run(
    db: aiosqlite.Connection,
    project_id: str,
    pr_id: str,
    iteration: int,
) -> QALoopRun:
    """Insert a new qa_loop_runs row with status='running'."""
    now = _now()
    run = QALoopRun(
        id=_uuid(),
        project_id=project_id,
        pr_id=pr_id,
        iteration=iteration,
        status="running",
        failure_count=0,
        created_at=now,
        updated_at=now,
    )
    await db.execute(
        """INSERT INTO qa_loop_runs
           (id, project_id, pr_id, iteration, status, failure_count,
            test_output, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run.id,
            run.project_id,
            run.pr_id,
            run.iteration,
            run.status,
            run.failure_count,
            run.test_output,
            run.created_at,
            run.updated_at,
        ),
    )
    await db.commit()
    return run


async def update_qa_loop_run(
    db: aiosqlite.Connection,
    run_id: str,
    *,
    status: str | None = None,
    failure_count: int | None = None,
    test_output: str | None = None,
) -> None:
    """Update fields on a qa_loop_runs row."""
    sets: list[str] = []
    params: list[Any] = []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if failure_count is not None:
        sets.append("failure_count = ?")
        params.append(failure_count)
    if test_output is not None:
        sets.append("test_output = ?")
        params.append(test_output)
    if not sets:
        return
    sets.append("updated_at = ?")
    params.append(_now())
    params.append(run_id)
    await db.execute(
        f"UPDATE qa_loop_runs SET {', '.join(sets)} WHERE id = ?",  # noqa: S608
        params,
    )
    await db.commit()


async def get_latest_qa_loop_run(
    db: aiosqlite.Connection,
    pr_id: str,
) -> QALoopRun | None:
    """Return the most recent qa_loop_run for a PR, or None."""
    cursor = await db.execute(
        "SELECT * FROM qa_loop_runs WHERE pr_id = ? ORDER BY iteration DESC LIMIT 1",
        (pr_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_qa_loop_run(row)


async def list_qa_loop_runs(
    db: aiosqlite.Connection,
    pr_id: str,
) -> list[QALoopRun]:
    """Return all qa_loop_runs for a PR ordered by iteration."""
    cursor = await db.execute(
        "SELECT * FROM qa_loop_runs WHERE pr_id = ? ORDER BY iteration ASC",
        (pr_id,),
    )
    rows = await cursor.fetchall()
    return [_row_to_qa_loop_run(r) for r in rows]

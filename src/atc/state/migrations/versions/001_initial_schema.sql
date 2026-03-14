-- Migration: 001 initial_schema
-- Created: 2026-03-14
--
-- Initial database schema for ATC.

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
    parent_task_id  TEXT REFERENCES tasks(id),
    title           TEXT NOT NULL,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    priority        INTEGER DEFAULT 0,
    assigned_to     TEXT REFERENCES sessions(id),
    result          TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
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
    project_id      TEXT REFERENCES projects(id),
    session_id      TEXT REFERENCES sessions(id),
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

CREATE TABLE IF NOT EXISTS github_prs (
    id              TEXT PRIMARY KEY,
    project_id      TEXT REFERENCES projects(id),
    number          INTEGER NOT NULL,
    title           TEXT,
    status          TEXT,
    ci_status       TEXT,
    url             TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES projects(id),
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

CREATE TABLE IF NOT EXISTS failure_logs (
    id           TEXT PRIMARY KEY,
    level        TEXT NOT NULL,
    category     TEXT NOT NULL,
    project_id   TEXT REFERENCES projects(id),
    entity_type  TEXT,
    entity_id    TEXT,
    message      TEXT NOT NULL,
    context      TEXT NOT NULL,
    stack_trace  TEXT,
    resolved     INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL
);

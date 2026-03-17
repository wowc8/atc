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

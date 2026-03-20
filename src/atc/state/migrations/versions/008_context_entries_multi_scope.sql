-- Add multi-scope support and restricted flag to context_entries.
-- Existing rows become scope='project', restricted=1.

-- Ensure the old table exists (it may have been created via _SCHEMA_SQL bootstrap
-- rather than a numbered migration).
CREATE TABLE IF NOT EXISTS context_entries (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id),
    key         TEXT NOT NULL,
    entry_type  TEXT NOT NULL,
    value       TEXT NOT NULL,
    position    INTEGER NOT NULL DEFAULT 0,
    updated_by  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(project_id, key)
);

-- Add new columns
ALTER TABLE context_entries ADD COLUMN scope TEXT NOT NULL DEFAULT 'project';
ALTER TABLE context_entries ADD COLUMN session_id TEXT REFERENCES sessions(id);
ALTER TABLE context_entries ADD COLUMN restricted BOOLEAN DEFAULT 0;

-- Mark all pre-M3 entries as restricted internal context
UPDATE context_entries SET restricted = 1;

-- Drop the old unique constraint and create the new one.
-- SQLite doesn't support DROP CONSTRAINT, so we recreate the table.
CREATE TABLE context_entries_new (
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

INSERT INTO context_entries_new
    (id, scope, project_id, session_id, key, entry_type, value, restricted,
     position, updated_by, created_at, updated_at)
SELECT
    id, scope, project_id, session_id, key, entry_type, value, restricted,
    position, updated_by, created_at, updated_at
FROM context_entries;

DROP TABLE context_entries;
ALTER TABLE context_entries_new RENAME TO context_entries;

-- Use COALESCE to handle NULLs in the unique index (SQLite treats NULL as distinct)
CREATE UNIQUE INDEX IF NOT EXISTS idx_context_entries_unique_key
    ON context_entries(scope, COALESCE(project_id, ''), COALESCE(session_id, ''), key);

CREATE INDEX IF NOT EXISTS idx_context_entries_scope ON context_entries(scope);
CREATE INDEX IF NOT EXISTS idx_context_entries_project_id ON context_entries(project_id);
CREATE INDEX IF NOT EXISTS idx_context_entries_session_id ON context_entries(session_id);

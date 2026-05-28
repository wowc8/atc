-- Add provider and explicit scope metadata to sessions.
-- This is the first staged slice of the provider/session-scope refactor.

ALTER TABLE sessions ADD COLUMN provider TEXT NOT NULL DEFAULT 'claude_code';
ALTER TABLE sessions ADD COLUMN scope_type TEXT NOT NULL DEFAULT 'project';
ALTER TABLE sessions ADD COLUMN scope_id TEXT;

-- Backfill existing rows so old project-scoped sessions remain coherent.
UPDATE sessions
SET scope_type = 'project',
    scope_id = project_id
WHERE scope_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_sessions_scope ON sessions(scope_type, scope_id);
CREATE INDEX IF NOT EXISTS idx_sessions_provider ON sessions(provider);

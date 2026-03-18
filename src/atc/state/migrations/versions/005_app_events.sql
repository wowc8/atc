-- Structured app-level event logging table
CREATE TABLE IF NOT EXISTS app_events (
    id          TEXT PRIMARY KEY,
    level       TEXT NOT NULL,           -- debug|info|warning|error|critical
    category    TEXT NOT NULL,           -- session|task|error|cost|system
    message     TEXT NOT NULL,
    detail      TEXT,                    -- JSON: arbitrary event-specific data
    project_id  TEXT,
    session_id  TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_app_events_created_at ON app_events(created_at);
CREATE INDEX IF NOT EXISTS idx_app_events_level ON app_events(level);
CREATE INDEX IF NOT EXISTS idx_app_events_category ON app_events(category);
CREATE INDEX IF NOT EXISTS idx_app_events_project_id ON app_events(project_id);

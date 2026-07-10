-- Provider helper subagent audit records and event timeline.
-- Helpers are provider-native workers subordinate to Tower/Leader/Ace; ATC stores
-- provider-neutral audit state even when helper visibility is hidden.
CREATE TABLE IF NOT EXISTS provider_helper_runs (
    id                 TEXT PRIMARY KEY,
    provider           TEXT NOT NULL,
    helper_id          TEXT,
    parent_session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    parent_role        TEXT NOT NULL CHECK(parent_role IN ('tower', 'leader', 'ace')),
    project_id         TEXT REFERENCES projects(id),
    task_id            TEXT,
    purpose            TEXT NOT NULL,
    visibility         TEXT NOT NULL DEFAULT 'hidden' CHECK(visibility IN ('hidden', 'summary', 'full')),
    status             TEXT NOT NULL DEFAULT 'requested' CHECK(status IN ('requested', 'running', 'completed', 'failed', 'cancelled')),
    started_at         TEXT NOT NULL,
    finished_at        TEXT,
    summary            TEXT,
    prompt_text        TEXT,
    output_text        TEXT,
    metadata_json      TEXT,
    error              TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_helper_events (
    id             TEXT PRIMARY KEY,
    helper_run_id  TEXT NOT NULL REFERENCES provider_helper_runs(id) ON DELETE CASCADE,
    event_type     TEXT NOT NULL,
    timestamp      TEXT NOT NULL,
    message        TEXT,
    payload_json   TEXT,
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_provider_helper_runs_parent
    ON provider_helper_runs(parent_session_id, parent_role, status);
CREATE INDEX IF NOT EXISTS idx_provider_helper_runs_project
    ON provider_helper_runs(project_id, started_at);
CREATE INDEX IF NOT EXISTS idx_provider_helper_events_run
    ON provider_helper_events(helper_run_id, timestamp);

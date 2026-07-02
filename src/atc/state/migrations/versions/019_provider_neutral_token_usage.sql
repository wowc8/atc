-- Provider-neutral token usage telemetry foundation.
ALTER TABLE usage_events ADD COLUMN provider TEXT;
ALTER TABLE usage_events ADD COLUMN source TEXT;
ALTER TABLE usage_events ADD COLUMN cached_input_tokens INTEGER;
ALTER TABLE usage_events ADD COLUMN reasoning_output_tokens INTEGER;
ALTER TABLE usage_events ADD COLUMN total_tokens INTEGER;
ALTER TABLE usage_events ADD COLUMN external_session_id TEXT;
ALTER TABLE usage_events ADD COLUMN source_event_id TEXT;
ALTER TABLE usage_events ADD COLUMN source_file TEXT;
ALTER TABLE usage_events ADD COLUMN source_offset INTEGER;
ALTER TABLE usage_events ADD COLUMN raw_usage_json TEXT;

CREATE INDEX IF NOT EXISTS idx_usage_events_tokens_recorded
    ON usage_events(event_type, recorded_at);
CREATE INDEX IF NOT EXISTS idx_usage_events_provider_source
    ON usage_events(provider, source, external_session_id);

CREATE TABLE IF NOT EXISTS usage_source_offsets (
    provider                      TEXT NOT NULL,
    source_key                    TEXT NOT NULL,
    external_session_id           TEXT,
    byte_offset                   INTEGER NOT NULL DEFAULT 0,
    last_input_tokens             INTEGER NOT NULL DEFAULT 0,
    last_cached_input_tokens      INTEGER NOT NULL DEFAULT 0,
    last_output_tokens            INTEGER NOT NULL DEFAULT 0,
    last_reasoning_output_tokens  INTEGER NOT NULL DEFAULT 0,
    last_total_tokens             INTEGER NOT NULL DEFAULT 0,
    created_at                    TEXT NOT NULL,
    updated_at                    TEXT NOT NULL,
    PRIMARY KEY (provider, source_key)
);

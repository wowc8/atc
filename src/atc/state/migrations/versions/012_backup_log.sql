-- Migration: 012 backup_log
-- Created: 2026-03-22
--
-- Adds the backup_log table for recording local and remote backup activity.

CREATE TABLE IF NOT EXISTS backup_log (
    id           TEXT PRIMARY KEY,
    backup_type  TEXT NOT NULL,   -- local | dropbox | gdrive
    status       TEXT NOT NULL,   -- success | failed
    path         TEXT,            -- local path or remote path
    size_bytes   INTEGER,
    error        TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backup_log_created_at ON backup_log(created_at);
CREATE INDEX IF NOT EXISTS idx_backup_log_status ON backup_log(status);

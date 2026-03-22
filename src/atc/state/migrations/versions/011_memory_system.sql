-- Migration: 011 memory_system
-- Created: 2026-03-22
--
-- Adds the three-layer memory system:
--   1. tower_memory.embedding — float32 BLOB for vector similarity search
--   2. ace_stm — Ace short-term memory (per-session progress snapshots)
--   3. memory_consolidation_runs — audit log for LTM consolidation jobs
--   4. tower_memory_fts — FTS5 virtual table for full-text search

-- Add embedding column to existing tower_memory table
-- (migration runner applies each file exactly once, so no IF NOT EXISTS needed)
ALTER TABLE tower_memory ADD COLUMN embedding BLOB;

-- Ace short-term memory: one row per live session, upserted every 5 tool calls
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

-- Consolidation run history: records each LTM synthesis job
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
CREATE INDEX IF NOT EXISTS idx_consolidation_runs_status
    ON memory_consolidation_runs(status);

-- FTS5 virtual table for full-text search over tower_memory entries
-- memory_id is stored unindexed to join back to tower_memory
CREATE VIRTUAL TABLE IF NOT EXISTS tower_memory_fts
    USING fts5(memory_id UNINDEXED, key, value);

-- Migration: 010 tower_memory_ensure
-- Created: 2026-03-22
--
-- Ensures the tower_memory table exists with the baseline schema before
-- migration 011 adds the embedding column.  The initial schema (001) creates
-- this table, but some test environments set up partial schemas that skip it.

CREATE TABLE IF NOT EXISTS tower_memory (
    id          TEXT PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    value       TEXT NOT NULL,
    project_id  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Migration: 010 qa_loop
-- Created: 2026-03-22
--
-- Add QA loop support: qa_status column on github_prs and qa_loop_runs table.

-- Track QA automation lifecycle for each PR.
ALTER TABLE github_prs ADD COLUMN qa_status TEXT NOT NULL DEFAULT 'pending';

-- One row per QA iteration attempt (test run) for a PR.
CREATE TABLE IF NOT EXISTS qa_loop_runs (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id),
    pr_id           TEXT NOT NULL REFERENCES github_prs(id),
    iteration       INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running',  -- running|passed|failed
    failure_count   INTEGER NOT NULL DEFAULT 0,
    test_output     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_qa_loop_runs_pr_id ON qa_loop_runs(pr_id);
CREATE INDEX IF NOT EXISTS idx_qa_loop_runs_project_id ON qa_loop_runs(project_id);

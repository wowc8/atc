-- Migration: 009 budget_constraints
-- Created: 2026-03-22
--
-- Add performance indexes on project_budgets and usage_events for
-- the budget enforcer and usage analytics queries.

-- Index for per-project usage aggregation (event_type + project filter)
CREATE INDEX IF NOT EXISTS idx_usage_events_project_type
    ON usage_events(project_id, event_type, recorded_at);

-- Index for time-range scans on usage_events (used by budget enforcer)
CREATE INDEX IF NOT EXISTS idx_usage_events_recorded_at
    ON usage_events(recorded_at);

-- Index for model-level aggregation (token/cost by model)
CREATE INDEX IF NOT EXISTS idx_usage_events_model
    ON usage_events(model, event_type);

-- Index for quick project_budgets lookups (already PK but let's be explicit)
CREATE INDEX IF NOT EXISTS idx_project_budgets_status
    ON project_budgets(current_status);

-- Index for GitHub PR queries by project + status
CREATE INDEX IF NOT EXISTS idx_github_prs_project_status
    ON github_prs(project_id, status);

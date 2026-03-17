-- Add agent_provider column to projects table.
-- Defaults to 'claude_code' for backward compatibility.
ALTER TABLE projects ADD COLUMN agent_provider TEXT NOT NULL DEFAULT 'claude_code';

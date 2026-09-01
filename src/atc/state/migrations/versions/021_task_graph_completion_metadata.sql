-- Add task completion metadata for durable Kanban timestamps.
ALTER TABLE task_graphs ADD COLUMN completed_at TEXT;

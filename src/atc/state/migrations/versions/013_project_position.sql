-- Add position column to projects table for drag-to-reorder support.
ALTER TABLE projects ADD COLUMN position INTEGER DEFAULT 0;

-- Assign sequential positions based on created_at order (oldest = 0).
UPDATE projects
SET position = (
    SELECT COUNT(*)
    FROM projects p2
    WHERE p2.created_at < projects.created_at
      OR (p2.created_at = projects.created_at AND p2.id < projects.id)
);

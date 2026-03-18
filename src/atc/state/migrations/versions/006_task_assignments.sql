-- Idempotent task assignments with state machine guards.
--
-- Creates a task_assignments table to track individual assignment attempts
-- with an assignment_id for idempotency.  Duplicate assignment_ids are
-- silently ignored (ON CONFLICT DO NOTHING at the application layer).

CREATE TABLE IF NOT EXISTS task_assignments (
    id              TEXT PRIMARY KEY,
    task_graph_id   TEXT NOT NULL REFERENCES task_graphs(id) ON DELETE CASCADE,
    ace_session_id  TEXT NOT NULL,
    assignment_id   TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'assigned',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_assignments_task_graph
    ON task_assignments(task_graph_id);
CREATE INDEX IF NOT EXISTS idx_task_assignments_ace_session
    ON task_assignments(ace_session_id);

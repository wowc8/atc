-- Add provider-neutral Ace dispatch truth to task assignments.
--
-- Assignment lifecycle (assigned/working/done/failed) remains separate from
-- runtime delivery truth so task work is not promoted merely because an Ace
-- session exists or a prompt was queued.

ALTER TABLE task_assignments
    ADD COLUMN dispatch_delivery_state TEXT NOT NULL DEFAULT 'queued_unverified';

ALTER TABLE task_assignments
    ADD COLUMN dispatch_verified INTEGER NOT NULL DEFAULT 0;

ALTER TABLE task_assignments
    ADD COLUMN last_activity_at TEXT;

ALTER TABLE task_assignments
    ADD COLUMN assigned_task_id TEXT;

ALTER TABLE task_assignments
    ADD COLUMN blocker_reason TEXT;

UPDATE task_assignments
SET assigned_task_id = task_graph_id
WHERE assigned_task_id IS NULL;

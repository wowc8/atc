-- Phase 10: Ace assignment acceptance truth.
--
-- Adds provider-neutral evidence reported by the Ace itself so Leader→Ace
-- handoff can distinguish assignment/session existence from assignment acceptance.

ALTER TABLE task_assignments
    ADD COLUMN ace_reported_active INTEGER NOT NULL DEFAULT 0;

ALTER TABLE task_assignments
    ADD COLUMN assignment_accepted INTEGER NOT NULL DEFAULT 0;

ALTER TABLE task_assignments
    ADD COLUMN assignment_accepted_at TEXT;

ALTER TABLE task_assignments
    ADD COLUMN acceptance_message TEXT;

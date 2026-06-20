-- Phase 11: Leader→Ace startup readiness and artifact routing parity.
ALTER TABLE task_assignments ADD COLUMN startup_readiness_state TEXT NOT NULL DEFAULT 'startup_handshake_pending';
ALTER TABLE task_assignments ADD COLUMN artifact_path TEXT;
ALTER TABLE task_assignments ADD COLUMN artifact_kind TEXT;
ALTER TABLE task_assignments ADD COLUMN artifact_ready INTEGER NOT NULL DEFAULT 0;
ALTER TABLE task_assignments ADD COLUMN artifact_reported_at TEXT;

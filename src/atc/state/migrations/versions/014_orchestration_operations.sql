CREATE TABLE IF NOT EXISTS orchestration_operations (
  operation_id TEXT PRIMARY KEY,
  operation_type TEXT NOT NULL,
  session_id TEXT,
  request_payload TEXT NOT NULL,
  response_payload TEXT,
  status TEXT NOT NULL DEFAULT 'accepted',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orchestration_operations_type
  ON orchestration_operations(operation_type, created_at);

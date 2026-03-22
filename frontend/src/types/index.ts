/** Shared TypeScript interfaces for ATC. */

export interface Project {
  id: string;
  name: string;
  description: string | null;
  repo_path: string | null;
  github_repo: string | null;
  agent_provider: "claude_code" | "opencode";
  status: "active" | "paused" | "archived";
  position?: number;
  created_at: string;
  updated_at: string;
}

export interface AgentProviderConfig {
  default: string;
  opencode_url: string;
  tmux_session: string;
  claude_command: string;
}

export interface ProviderInfo {
  name: string;
  supports_streaming: boolean;
  supports_tool_use: boolean;
  context_window: number;
  model: string;
}

export interface Leader {
  id: string;
  project_id: string;
  session_id: string | null;
  context: Record<string, unknown> | null;
  goal: string | null;
  status: "idle" | "planning" | "managing" | "paused" | "error";
  created_at: string;
  updated_at: string;
}

export interface Session {
  id: string;
  project_id: string;
  session_type: "ace" | "manager" | "tower";
  name: string;
  status:
    | "idle"
    | "connecting"
    | "working"
    | "waiting"
    | "paused"
    | "disconnected"
    | "error";
  task_id: string | null;
  host: string | null;
  tmux_session: string | null;
  tmux_pane: string | null;
  alternate_on: boolean;
  auto_accept: boolean;
  created_at: string;
  updated_at: string;
}

export interface Task {
  id: string;
  project_id: string;
  leader_id: string;
  title: string;
  status:
    | "pending"
    | "assigned"
    | "in_progress"
    | "blocked"
    | "done"
    | "cancelled";
  parent_task_id: string | null;
  description: string | null;
  priority: number;
  assigned_to: string | null;
  result: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface TaskGraph {
  id: string;
  project_id: string;
  title: string;
  description: string | null;
  status: "todo" | "in_progress" | "done";
  assigned_ace_id: string | null;
  dependencies: string[] | null;
  created_at: string;
  updated_at: string;
}

export interface Budget {
  project_id: string;
  daily_token_limit: number | null;
  monthly_cost_limit: number | null;
  warn_threshold: number;
  current_status: "ok" | "warn" | "exceeded";
  updated_at: string;
}

export interface Notification {
  id: string;
  project_id: string | null;
  level: "info" | "warning" | "error" | "budget";
  message: string;
  read: boolean;
  created_at: string;
}

export interface UsageSummary {
  today_cost: number;
  month_cost: number;
  today_tokens: number;
  month_tokens: number;
}

export interface GitHubSummary {
  open_prs: number;
  merged_today: number;
  ci_pass_rate: number;
}

export type TowerStatusValue = "idle" | "planning" | "warning" | "error";

export interface TowerStatus {
  status: TowerStatusValue;
  message: string;
  active_projects: number;
}

export interface TowerDetail {
  state: string;
  current_goal: string | null;
  current_project_id: string | null;
  current_session_id: string | null;
  leader_session_id: string | null;
  leader_activity_preview: string | null;
}

export interface TowerProgress {
  project_id: string | null;
  total: number;
  done: number;
  in_progress: number;
  todo: number;
  progress_pct: number;
  all_done: boolean;
}

export interface FailureLog {
  id: string;
  level: "info" | "warning" | "error" | "critical";
  category: string;
  message: string;
  context: Record<string, unknown> | null;
  project_id: string | null;
  entity_type: string | null;
  entity_id: string | null;
  stack_trace: string | null;
  resolved: boolean;
  created_at: string;
}

export interface SessionHeartbeat {
  session_id: string;
  health: "alive" | "stale" | "stopped";
  last_heartbeat_at: string;
  registered_at: string;
  updated_at: string;
}

export interface AppEvent {
  id: string;
  level: "debug" | "info" | "warning" | "error" | "critical";
  category: "session" | "task" | "error" | "cost" | "system";
  message: string;
  detail: Record<string, unknown> | null;
  project_id: string | null;
  session_id: string | null;
  created_at: string;
}

export type ContextScope = "global" | "project" | "tower" | "leader" | "ace";

export interface ContextEntry {
  id: string;
  scope: ContextScope;
  project_id: string | null;
  session_id: string | null;
  key: string;
  entry_type: string;
  value: string;
  restricted: boolean;
  position: number;
  updated_by: string;
  created_at: string;
  updated_at: string;
}

export interface FeatureFlag {
  id: string;
  key: string;
  name: string;
  description: string | null;
  enabled: boolean;
  metadata: string | null;
  created_at: string;
  updated_at: string;
}

export interface AppState {
  projects: Project[];
  leaders: Record<string, Leader>;
  sessions: Session[];
  tasks: Record<string, Task[]>;
  taskGraphs: Record<string, TaskGraph[]>;
  budgets: Record<string, Budget>;
  brainStatus: TowerStatus;
  towerDetail: TowerDetail;
  towerProgress: TowerProgress;
  notifications: Notification[];
  failureLogs: FailureLog[];
  usage: UsageSummary;
  github: Record<string, GitHubSummary>;
  heartbeats: Record<string, SessionHeartbeat>;
  selectedProjectId: string | null;
  selectedSessionId: string | null;
}

/** Shared TypeScript interfaces for ATC. */

export interface Project {
  id: string;
  name: string;
  description: string | null;
  repo_path: string | null;
  github_repo: string | null;
  status: "active" | "paused" | "archived";
  created_at: string;
  updated_at: string;
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
  session_type: "ace" | "manager";
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

export interface AppState {
  projects: Project[];
  leaders: Record<string, Leader>;
  sessions: Session[];
  tasks: Record<string, Task[]>;
  budgets: Record<string, Budget>;
  brainStatus: TowerStatus;
  notifications: Notification[];
  usage: UsageSummary;
  github: Record<string, GitHubSummary>;
  selectedProjectId: string | null;
  selectedSessionId: string | null;
}

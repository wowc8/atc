import {
  createContext,
  useContext,
  useReducer,
  useEffect,
  useCallback,
  type ReactNode,
} from "react";
import type {
  AppState,
  Project,
  Leader,
  Session,
  SessionHeartbeat,
  Notification,
  FailureLog,
  Task,
  TaskGraph,
  Budget,
  UsageSummary,
  GitHubSummary,
  TowerStatus,
  TowerDetail,
  TowerProgress,
} from "../types";
import { useWebSocket, type WsMessage } from "../hooks/useWebSocket";
import { api } from "../utils/api";

// ---------------------------------------------------------------------------
// Initial state
// ---------------------------------------------------------------------------
const initialState: AppState = {
  projects: [],
  leaders: {},
  sessions: [],
  tasks: {},
  taskGraphs: {},
  budgets: {},
  brainStatus: { status: "idle", message: "", active_projects: 0 },
  towerDetail: {
    state: "idle",
    current_goal: null,
    current_project_id: null,
    current_session_id: null,
    leader_session_id: null,
    leader_activity_preview: null,
  },
  towerProgress: {
    project_id: null,
    total: 0,
    done: 0,
    in_progress: 0,
    todo: 0,
    progress_pct: 0,
    all_done: false,
  },
  notifications: [],
  failureLogs: [],
  usage: { today_cost: 0, month_cost: 0, today_tokens: 0, month_tokens: 0 },
  github: {},
  heartbeats: {},
  selectedProjectId: null,
  selectedSessionId: null,
};

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------
type Action =
  | { type: "SET_STATE"; payload: Partial<AppState> }
  | { type: "SET_PROJECTS"; payload: Project[] }
  | { type: "SET_LEADERS"; payload: Record<string, Leader> }
  | { type: "SET_SESSIONS"; payload: Session[] }
  | { type: "SET_TASKS"; payload: Record<string, Task[]> }
  | { type: "SET_TASK_GRAPHS"; payload: Record<string, TaskGraph[]> }
  | { type: "SET_BUDGETS"; payload: Record<string, Budget> }
  | { type: "SET_BRAIN_STATUS"; payload: TowerStatus }
  | { type: "SET_TOWER_DETAIL"; payload: Partial<TowerDetail> }
  | { type: "SET_NOTIFICATIONS"; payload: Notification[] }
  | { type: "SET_USAGE"; payload: UsageSummary }
  | { type: "SET_GITHUB"; payload: Record<string, GitHubSummary> }
  | { type: "SELECT_PROJECT"; payload: string | null }
  | { type: "SELECT_SESSION"; payload: string | null }
  | { type: "ADD_NOTIFICATION"; payload: Notification }
  | { type: "MARK_NOTIFICATION_READ"; payload: string }
  | { type: "SET_FAILURE_LOGS"; payload: FailureLog[] }
  | { type: "ADD_FAILURE_LOG"; payload: FailureLog }
  | { type: "RESOLVE_FAILURE_LOG"; payload: string }
  | { type: "SET_TOWER_PROGRESS"; payload: TowerProgress }
  | { type: "SET_HEARTBEATS"; payload: Record<string, SessionHeartbeat> }
  | { type: "UPDATE_HEARTBEAT"; payload: SessionHeartbeat }
  | { type: "UPDATE_SESSION_STATUS"; payload: { session_id: string; status: string } }
  | { type: "ADD_PROJECT"; payload: Project }
  | { type: "UPDATE_PROJECT"; payload: Project }
  | { type: "REMOVE_PROJECT"; payload: string }
  | { type: "ADD_SESSION"; payload: Session }
  | { type: "REMOVE_SESSION"; payload: string };

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "SET_STATE":
      return { ...state, ...action.payload };
    case "SET_PROJECTS":
      return { ...state, projects: action.payload };
    case "SET_LEADERS":
      return { ...state, leaders: action.payload };
    case "SET_SESSIONS":
      return { ...state, sessions: action.payload };
    case "SET_TASKS":
      return { ...state, tasks: action.payload };
    case "SET_TASK_GRAPHS":
      return { ...state, taskGraphs: action.payload };
    case "SET_BUDGETS":
      return { ...state, budgets: action.payload };
    case "SET_BRAIN_STATUS":
      return { ...state, brainStatus: action.payload };
    case "SET_TOWER_DETAIL":
      return {
        ...state,
        towerDetail: { ...state.towerDetail, ...action.payload },
      };
    case "SET_NOTIFICATIONS":
      return { ...state, notifications: action.payload };
    case "SET_USAGE":
      return { ...state, usage: action.payload };
    case "SET_GITHUB":
      return { ...state, github: action.payload };
    case "SELECT_PROJECT":
      return { ...state, selectedProjectId: action.payload };
    case "SELECT_SESSION":
      return { ...state, selectedSessionId: action.payload };
    case "ADD_NOTIFICATION":
      return {
        ...state,
        notifications: [action.payload, ...state.notifications],
      };
    case "MARK_NOTIFICATION_READ":
      return {
        ...state,
        notifications: state.notifications.map((n) =>
          n.id === action.payload ? { ...n, read: true } : n,
        ),
      };
    case "SET_FAILURE_LOGS":
      return { ...state, failureLogs: action.payload };
    case "ADD_FAILURE_LOG":
      return {
        ...state,
        failureLogs: [action.payload, ...state.failureLogs],
      };
    case "RESOLVE_FAILURE_LOG":
      return {
        ...state,
        failureLogs: state.failureLogs.map((f) =>
          f.id === action.payload ? { ...f, resolved: true } : f,
        ),
      };
    case "SET_TOWER_PROGRESS":
      return { ...state, towerProgress: action.payload };
    case "SET_HEARTBEATS":
      return { ...state, heartbeats: action.payload };
    case "UPDATE_HEARTBEAT":
      return {
        ...state,
        heartbeats: {
          ...state.heartbeats,
          [action.payload.session_id]: action.payload,
        },
      };
    case "UPDATE_SESSION_STATUS":
      return {
        ...state,
        sessions: state.sessions.map((s) =>
          s.id === action.payload.session_id
            ? { ...s, status: action.payload.status as Session["status"] }
            : s,
        ),
      };
    case "ADD_PROJECT":
      // Avoid duplicates — if project already exists, update it instead
      if (state.projects.some((p) => p.id === action.payload.id)) {
        return {
          ...state,
          projects: state.projects.map((p) =>
            p.id === action.payload.id ? action.payload : p,
          ),
        };
      }
      return { ...state, projects: [...state.projects, action.payload] };
    case "UPDATE_PROJECT":
      return {
        ...state,
        projects: state.projects.map((p) =>
          p.id === action.payload.id ? action.payload : p,
        ),
      };
    case "REMOVE_PROJECT":
      return {
        ...state,
        projects: state.projects.filter((p) => p.id !== action.payload),
      };
    case "ADD_SESSION":
      // Avoid duplicates
      if (state.sessions.some((s) => s.id === action.payload.id)) {
        return {
          ...state,
          sessions: state.sessions.map((s) =>
            s.id === action.payload.id ? action.payload : s,
          ),
        };
      }
      return { ...state, sessions: [...state.sessions, action.payload] };
    case "REMOVE_SESSION":
      return {
        ...state,
        sessions: state.sessions.filter((s) => s.id !== action.payload),
      };
  }
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------
interface AppContextValue {
  state: AppState;
  dispatch: React.Dispatch<Action>;
  fetchAll: () => Promise<void>;
}

const AppContext = createContext<AppContextValue | null>(null);

export function useAppContext(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useAppContext must be used within AppProvider");
  return ctx;
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------
interface AppProviderProps {
  children: ReactNode;
}

export function AppProvider({ children }: AppProviderProps) {
  const [state, dispatch] = useReducer(reducer, initialState);

  const fetchAll = useCallback(async () => {
    try {
      const projects = await api.get<Project[]>("/projects");
      dispatch({ type: "SET_PROJECTS", payload: projects });

      // Fetch aces per project + leader per project
      const sessionResults = await Promise.allSettled(
        projects.map((p) => api.get<Session[]>(`/projects/${p.id}/aces`)),
      );
      const allSessions: Session[] = [];
      for (const r of sessionResults) {
        if (r.status === "fulfilled") allSessions.push(...r.value);
      }
      dispatch({ type: "SET_SESSIONS", payload: allSessions });

      const leaderResults = await Promise.allSettled(
        projects.map((p) => api.get<Leader>(`/projects/${p.id}/manager`)),
      );
      const leaders: Record<string, Leader> = {};
      for (let i = 0; i < projects.length; i++) {
        const r = leaderResults[i]!;
        if (r.status === "fulfilled") leaders[projects[i]!.id] = r.value;
      }
      dispatch({ type: "SET_LEADERS", payload: leaders });

      const taskGraphResults = await Promise.allSettled(
        projects.map((p) =>
          api.get<TaskGraph[]>(`/projects/${p.id}/task-graphs`),
        ),
      );
      const taskGraphs: Record<string, TaskGraph[]> = {};
      for (let i = 0; i < projects.length; i++) {
        const r = taskGraphResults[i]!;
        if (r.status === "fulfilled") taskGraphs[projects[i]!.id] = r.value;
      }
      dispatch({ type: "SET_TASK_GRAPHS", payload: taskGraphs });

      // Fetch heartbeats
      const heartbeatList = await api.get<SessionHeartbeat[]>("/heartbeat");
      const heartbeats: Record<string, SessionHeartbeat> = {};
      for (const hb of heartbeatList) {
        heartbeats[hb.session_id] = hb;
      }
      dispatch({ type: "SET_HEARTBEATS", payload: heartbeats });

      // Fetch failure logs
      const failureLogs = await api.get<FailureLog[]>("/failure-logs?limit=200");
      dispatch({ type: "SET_FAILURE_LOGS", payload: failureLogs });

      // Fetch tower status for panel state
      const towerStatus = await api.get<{
        state: string;
        current_goal: string | null;
        current_project_id: string | null;
        current_session_id: string | null;
        leader_session_id: string | null;
      }>("/tower/status");
      dispatch({
        type: "SET_TOWER_DETAIL",
        payload: {
          state: towerStatus.state,
          current_goal: towerStatus.current_goal,
          current_project_id: towerStatus.current_project_id,
          current_session_id: towerStatus.current_session_id,
          leader_session_id: towerStatus.leader_session_id ?? null,
          leader_activity_preview: null,
        },
      });
    } catch {
      /* backend may not be running yet — silent fail */
    }
  }, []);

  const handleWsMessage = useCallback((msg: WsMessage) => {
    if (msg.channel === "state") {
      const data = msg.data as Record<string, unknown>;
      // Handle individual session status updates from the backend
      if (data.sessions_updated && typeof data.session_id === "string" && typeof data.new_status === "string") {
        dispatch({
          type: "UPDATE_SESSION_STATUS",
          payload: { session_id: data.session_id, status: data.new_status },
        });
      } else if (data.project_created && data.project) {
        dispatch({ type: "ADD_PROJECT", payload: data.project as Project });
      } else if (data.project_updated && data.project) {
        dispatch({ type: "UPDATE_PROJECT", payload: data.project as Project });
      } else if (data.project_deleted && typeof data.project_id === "string") {
        dispatch({ type: "REMOVE_PROJECT", payload: data.project_id });
      } else if (data.session_created && data.session) {
        dispatch({ type: "ADD_SESSION", payload: data.session as Session });
      } else {
        dispatch({ type: "SET_STATE", payload: data as Partial<AppState> });
      }
    } else if (msg.channel === "tower") {
      const data = msg.data as Record<string, unknown>;
      if (data.type === "state_changed") {
        dispatch({
          type: "SET_TOWER_DETAIL",
          payload: {
            state: data.new_state as string,
            current_goal: (data.goal as string) ?? null,
            current_project_id: (data.project_id as string) ?? null,
          },
        });
      } else if (data.type === "tower_session") {
        dispatch({
          type: "SET_TOWER_DETAIL",
          payload: {
            current_session_id: (data.session_id as string) ?? null,
          },
        });
      } else if (data.type === "leader_status") {
        dispatch({
          type: "SET_TOWER_DETAIL",
          payload: {
            leader_session_id: (data.session_id as string) ?? null,
          },
        });
      } else if (data.type === "progress") {
        dispatch({
          type: "SET_TOWER_PROGRESS",
          payload: {
            project_id: (data.project_id as string) ?? null,
            total: (data.total as number) ?? 0,
            done: (data.done as number) ?? 0,
            in_progress: (data.in_progress as number) ?? 0,
            todo: (data.todo as number) ?? 0,
            progress_pct: (data.progress_pct as number) ?? 0,
            all_done: (data.all_done as boolean) ?? false,
          },
        });
      } else if (data.type === "leader_activity") {
        dispatch({
          type: "SET_TOWER_DETAIL",
          payload: {
            leader_activity_preview: (data.preview as string) ?? null,
          },
        });
      }
    } else if (msg.channel === "heartbeat") {
      const data = msg.data as {
        session_id: string;
        health: "alive" | "stale" | "stopped";
        last_heartbeat_at?: string;
      };
      if (data.session_id) {
        dispatch({
          type: "UPDATE_HEARTBEAT",
          payload: {
            session_id: data.session_id,
            health: data.health,
            last_heartbeat_at: data.last_heartbeat_at ?? new Date().toISOString(),
            registered_at: "",
            updated_at: new Date().toISOString(),
          },
        });
      }
    } else if (msg.channel === "failure_logs") {
      const data = msg.data as Record<string, unknown>;
      if (data.new) {
        dispatch({ type: "ADD_FAILURE_LOG", payload: data.new as FailureLog });
      }
      if (typeof data.resolved === "string") {
        dispatch({ type: "RESOLVE_FAILURE_LOG", payload: data.resolved });
      }
    }
  }, []);

  useWebSocket({
    channels: ["state", "failure_logs", "tower", "heartbeat"],
    onMessage: handleWsMessage,
  });

  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  return (
    <AppContext.Provider value={{ state, dispatch, fetchAll }}>
      {children}
    </AppContext.Provider>
  );
}

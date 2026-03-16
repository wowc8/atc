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
  Notification,
  Task,
  Budget,
  UsageSummary,
  GitHubSummary,
  TowerStatus,
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
  budgets: {},
  brainStatus: { status: "idle", message: "", active_projects: 0 },
  notifications: [],
  usage: { today_cost: 0, month_cost: 0, today_tokens: 0, month_tokens: 0 },
  github: {},
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
  | { type: "SET_BUDGETS"; payload: Record<string, Budget> }
  | { type: "SET_BRAIN_STATUS"; payload: TowerStatus }
  | { type: "SET_NOTIFICATIONS"; payload: Notification[] }
  | { type: "SET_USAGE"; payload: UsageSummary }
  | { type: "SET_GITHUB"; payload: Record<string, GitHubSummary> }
  | { type: "SELECT_PROJECT"; payload: string | null }
  | { type: "SELECT_SESSION"; payload: string | null }
  | { type: "ADD_NOTIFICATION"; payload: Notification }
  | { type: "MARK_NOTIFICATION_READ"; payload: string };

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
    case "SET_BUDGETS":
      return { ...state, budgets: action.payload };
    case "SET_BRAIN_STATUS":
      return { ...state, brainStatus: action.payload };
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
      const [projects, sessions, notifications] = await Promise.all([
        api.get<Project[]>("/projects"),
        api.get<Session[]>("/sessions"),
        api.get<Notification[]>("/notifications"),
      ]);
      dispatch({ type: "SET_PROJECTS", payload: projects });
      dispatch({ type: "SET_SESSIONS", payload: sessions });
      dispatch({ type: "SET_NOTIFICATIONS", payload: notifications });
    } catch {
      /* backend may not be running yet — silent fail */
    }
  }, []);

  const handleWsMessage = useCallback((msg: WsMessage) => {
    if (msg.channel === "state") {
      const data = msg.data as Partial<AppState>;
      dispatch({ type: "SET_STATE", payload: data });
    }
  }, []);

  useWebSocket({
    channels: ["state"],
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

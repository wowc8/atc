import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import TowerConsole from "../TowerConsole";
import type { AppState, Project, TowerDetail } from "../../../types";

// Mock useTerminal since it requires WebSocket/xterm.
vi.mock("../../../hooks/useTerminal", () => ({
  useTerminal: () => ({
    attachRef: vi.fn(),
    fit: vi.fn(),
  }),
}));

const dispatch = vi.fn();
let mockState: AppState;

vi.mock("../../../context/AppContext", () => ({
  useAppContext: () => ({ state: mockState, dispatch }),
}));

function project(overrides: Partial<Project> = {}): Project {
  return {
    id: "proj-1",
    name: "Project",
    description: null,
    repo_path: null,
    github_repo: null,
    agent_provider: "claude_code",
    status: "active",
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
    ...overrides,
  };
}

function tower(overrides: Partial<TowerDetail> = {}): TowerDetail {
  return {
    state: "idle",
    current_goal: null,
    current_project_id: null,
    current_session_id: null,
    leader_session_id: null,
    leader_activity_preview: null,
    ...overrides,
  };
}

function setMockState(overrides: Partial<AppState> = {}) {
  mockState = {
    projects: [],
    sessions: [],
    leaders: {},
    tasks: {},
    taskGraphs: {},
    budgets: {},
    notifications: [],
    towerDetail: tower(),
    towerProgress: { project_id: null, done: 0, total: 0, in_progress: 0, todo: 0, progress_pct: 0, all_done: false },
    brainStatus: { status: "idle", message: "Idle", active_projects: 0 },
    failureLogs: [],
    usage: { today_tokens: 0, month_tokens: 0 },
    github: {},
    heartbeats: {},
    selectedProjectId: null,
    selectedSessionId: null,
    ...overrides,
  };
}

beforeEach(() => {
  dispatch.mockClear();
  setMockState();
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({}), { status: 200 }),
  );
});

describe("TowerConsole", () => {
  it("renders the tower console", () => {
    render(<TowerConsole />);
    expect(screen.getByTestId("tower-console")).toBeInTheDocument();
  });

  it("shows Tower label in the header", () => {
    render(<TowerConsole />);
    expect(screen.getByText("Tower")).toBeInTheDocument();
  });

  it("shows Start button when idle", () => {
    render(<TowerConsole />);
    expect(screen.getByTestId("tower-console-start")).toBeInTheDocument();
    expect(screen.getByTestId("tower-console-start")).toHaveTextContent("Start");
  });

  it("shows goal input and project select when idle for non-terminal providers", () => {
    render(<TowerConsole />);
    expect(screen.getByTestId("tower-console-goal")).toBeInTheDocument();
    expect(screen.getByTestId("tower-console-project")).toBeInTheDocument();
  });

  it("shows status badge", () => {
    render(<TowerConsole />);
    expect(screen.getByText("idle")).toBeInTheDocument();
  });

  it("does not show terminal when idle", () => {
    render(<TowerConsole />);
    expect(screen.queryByTestId("tower-console-terminal")).not.toBeInTheDocument();
  });

  it("disables Start when no project is selected", () => {
    render(<TowerConsole />);
    expect(screen.getByTestId("tower-console-start")).toBeDisabled();
  });

  it("treats codex as a terminal-backed provider", () => {
    setMockState({ projects: [project({ name: "Codex Project", agent_provider: "codex" })] });
    render(<TowerConsole />);
    expect(screen.queryByTestId("tower-console-goal")).not.toBeInTheDocument();
  });

  it("shows a project-context restart warning when Tower is attached elsewhere", () => {
    setMockState({
      projects: [
        project({ id: "proj-1", name: "Codex Project", agent_provider: "codex", status: "active" }),
        project({ id: "proj-2", name: "Claude Project", status: "paused" }),
      ],
      towerDetail: tower({
        state: "managing",
        current_project_id: "proj-2",
        current_session_id: "tower-1",
      }),
    });
    render(<TowerConsole />);
    expect(screen.getByTestId("tower-console-provider-mismatch")).toBeInTheDocument();
    expect(screen.getByTestId("tower-console-restart-provider")).toHaveTextContent(
      "Restart Tower for selected project",
    );
  });
});

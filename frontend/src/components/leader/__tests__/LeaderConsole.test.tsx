import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LeaderConsole from "../LeaderConsole";
import type { AppState, Leader, Project } from "../../../types";

vi.mock("../../../hooks/useTerminal", () => ({
  useTerminal: () => ({ attachRef: vi.fn() }),
}));

vi.mock("../../dashboard/GitHubPanel", () => ({
  default: () => <div data-testid="github-panel" />,
}));

vi.mock("../BudgetPanel", () => ({
  default: () => <div data-testid="budget-panel" />,
}));

const dispatch = vi.fn();
let mockState: AppState;

vi.mock("../../../context/AppContext", () => ({
  useAppContext: () => ({ state: mockState, dispatch }),
}));

function setMockState(overrides: Partial<AppState> = {}) {
  mockState = {
    projects: [],
    sessions: [],
    tasks: {},
    taskGraphs: {},
    budgets: {},
    notifications: [],
    towerDetail: {
      state: "idle",
      current_goal: null,
      current_project_id: null,
      current_session_id: null,
      leader_session_id: null,
      leader_activity_preview: null,
    },
    towerProgress: { project_id: null, done: 0, total: 0, in_progress: 0, todo: 0, progress_pct: 0, all_done: false },
    brainStatus: { status: "idle", message: "Idle", active_projects: 0 },
    leaders: {},
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
  vi.restoreAllMocks();
  dispatch.mockClear();
  setMockState();
  vi.spyOn(globalThis, "fetch").mockImplementation(() =>
    Promise.resolve(new Response(JSON.stringify({}), { status: 200 })),
  );
});

const idleLeader: Leader = {
  id: "leader-1",
  project_id: "proj-1",
  session_id: null,
  context: null,
  goal: null,
  status: "idle",
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
};

const managingLeader: Leader = {
  ...idleLeader,
  status: "managing",
  session_id: "sess-1",
  goal: "Build the feature",
};

const planningLeader: Leader = {
  ...idleLeader,
  status: "planning",
  session_id: "sess-2",
};

const codexProject: Project = {
  id: "proj-1",
  name: "Codex Project",
  description: null,
  repo_path: null,
  github_repo: null,
  agent_provider: "codex",
  status: "active",
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
};

describe("LeaderConsole", () => {
  it("renders the leader console", () => {
    render(<LeaderConsole projectId="proj-1" leader={undefined} onRefresh={vi.fn()} />);
    expect(screen.getByTestId("leader-console")).toBeInTheDocument();
  });

  it("shows Start button when leader is idle", () => {
    render(<LeaderConsole projectId="proj-1" leader={idleLeader} onRefresh={vi.fn()} />);
    expect(screen.getByText("Start")).toBeInTheDocument();
  });

  it("shows Stop button when leader is managing", () => {
    render(<LeaderConsole projectId="proj-1" leader={managingLeader} onRefresh={vi.fn()} />);
    expect(screen.getByText("Stop")).toBeInTheDocument();
  });

  it("shows Stop button when leader is planning", () => {
    render(<LeaderConsole projectId="proj-1" leader={planningLeader} onRefresh={vi.fn()} />);
    expect(screen.getByText("Stop")).toBeInTheDocument();
  });

  it("shows Start button when leader is undefined", () => {
    render(<LeaderConsole projectId="proj-1" leader={undefined} onRefresh={vi.fn()} />);
    expect(screen.getByText("Start")).toBeInTheDocument();
  });

  it("shows goal input when not running", () => {
    render(<LeaderConsole projectId="proj-1" leader={idleLeader} onRefresh={vi.fn()} />);
    expect(screen.getByLabelText("Goal (optional)")).toBeInTheDocument();
  });

  it("hides goal input when running", () => {
    render(<LeaderConsole projectId="proj-1" leader={managingLeader} onRefresh={vi.fn()} />);
    expect(screen.queryByLabelText("Goal (optional)")).not.toBeInTheDocument();
  });

  it("shows terminal chrome when leader has a goal and is running", () => {
    render(<LeaderConsole projectId="proj-1" leader={managingLeader} onRefresh={vi.fn()} />);
    expect(screen.getByTestId("leader-console")).toBeInTheDocument();
    expect(screen.getByText("Stop")).toBeInTheDocument();
  });

  it("shows status badge for leader", () => {
    render(<LeaderConsole projectId="proj-1" leader={managingLeader} onRefresh={vi.fn()} />);
    expect(screen.getByText("managing")).toBeInTheDocument();
  });

  it("calls start API and awaits onRefresh when Start is clicked", async () => {
    const user = userEvent.setup();
    const onRefresh = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ status: "started", session_id: "sess-new" }), { status: 200 }),
      ),
    );

    render(<LeaderConsole projectId="proj-1" leader={idleLeader} onRefresh={onRefresh} />);
    await user.click(screen.getByText("Start"));

    await waitFor(() => {
      expect(onRefresh).toHaveBeenCalled();
    });
  });

  it("updates leader status to idle after stop API succeeds", async () => {
    const user = userEvent.setup();
    const onRefresh = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ status: "stopped" }), { status: 200 }),
      ),
    );

    render(<LeaderConsole projectId="proj-1" leader={managingLeader} onRefresh={onRefresh} />);
    await user.click(screen.getByText("Stop"));

    const confirmBtn = screen.getByTestId("confirm-popover-confirm");
    await user.click(confirmBtn);

    await waitFor(() => {
      expect(onRefresh).toHaveBeenCalled();
    });
  });

  it("shows Starting... while loading on start", async () => {
    const user = userEvent.setup();
    let resolvePost!: (v: Response) => void;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () => new Promise<Response>((r) => { resolvePost = r; }),
    );

    render(<LeaderConsole projectId="proj-1" leader={idleLeader} onRefresh={vi.fn()} />);
    await user.click(screen.getByText("Start"));
    expect(screen.getByText("Starting...")).toBeInTheDocument();

    resolvePost(new Response(JSON.stringify({ status: "started" }), { status: 200 }));
  });

  it("treats codex as a terminal-backed provider", () => {
    setMockState({ projects: [codexProject] });
    render(<LeaderConsole projectId="proj-1" leader={idleLeader} onRefresh={vi.fn()} project={codexProject} />);
    expect(screen.queryByLabelText("Goal (optional)")).not.toBeInTheDocument();
  });
});

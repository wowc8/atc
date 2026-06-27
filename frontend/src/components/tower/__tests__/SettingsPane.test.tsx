import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SettingsPane from "../SettingsPane";
import type { AppState, Project } from "../../../types";

vi.mock("../../settings/BackupPanel", () => ({
  BackupPanel: () => <div data-testid="backup-panel" />,
}));

vi.mock("../../settings/ResourceLimitsPanel", () => ({
  ResourceLimitsPanel: () => <div data-testid="resource-limits-panel" />,
}));

let mockState: AppState;

vi.mock("../../../context/AppContext", () => ({
  useAppContext: () => ({ state: mockState, dispatch: vi.fn() }),
}));

function project(overrides: Partial<Project> = {}): Project {
  return {
    id: "proj-1",
    name: "Alpha",
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

function setMockState(overrides: Partial<AppState> = {}) {
  mockState = {
    projects: [],
    sessions: [],
    leaders: {},
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
  setMockState();
});

describe("SettingsPane", () => {
  it("shows global provider status instead of project apply controls", async () => {
    setMockState({ projects: [project()] });
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            default: "codex",
            opencode_url: "http://localhost:4096",
            tmux_session: "atc",
            claude_command: "claude",
            codex_command: "codex",
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify([
            { name: "claude_code", supports_streaming: true, supports_tool_use: true, context_window: 200000, model: "claude" },
            { name: "codex", supports_streaming: true, supports_tool_use: true, context_window: 200000, model: "codex" },
          ]),
          { status: 200 },
        ),
      );

    render(<SettingsPane onClose={() => undefined} />);

    await waitFor(() => {
      expect(screen.getByTestId("provider-global-status")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("provider-action-project")).not.toBeInTheDocument();
    expect(screen.queryByTestId("provider-apply-project")).not.toBeInTheDocument();
    expect(screen.queryByTestId("provider-restart-tower")).not.toBeInTheDocument();
  });

  it("saves the global provider and shows restart/replacement messaging", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            default: "claude_code",
            opencode_url: "http://localhost:4096",
            tmux_session: "atc",
            claude_command: "claude",
            codex_command: "codex",
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify([
            { name: "claude_code", supports_streaming: true, supports_tool_use: true, context_window: 200000, model: "claude" },
            { name: "codex", supports_streaming: true, supports_tool_use: true, context_window: 200000, model: "codex" },
          ]),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            default: "codex",
            opencode_url: "http://localhost:4096",
            tmux_session: "atc",
            claude_command: "claude",
            codex_command: "codex",
          }),
          { status: 200 },
        ),
      );

    render(<SettingsPane onClose={() => undefined} />);

    await waitFor(() => {
      expect(screen.getByLabelText("Global Provider")).toBeInTheDocument();
    });

    await user.selectOptions(screen.getByLabelText("Global Provider"), "codex");

    await waitFor(() => {
      expect(
        screen.getByText(
          "Provider updated globally. Existing sessions were restarted or marked for replacement as needed.",
        ),
      ).toBeInTheDocument();
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/agent-provider",
      expect.objectContaining({ method: "PUT" }),
    );
  });
});

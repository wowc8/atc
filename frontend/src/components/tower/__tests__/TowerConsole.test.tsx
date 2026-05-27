import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import TowerConsole from "../TowerConsole";
import { renderWithProviders } from "../../../test/helpers";

// Mock useTerminal since it requires WebSocket/xterm
vi.mock("../../../hooks/useTerminal", () => ({
  useTerminal: () => ({
    attachRef: vi.fn(),
    fit: vi.fn(),
  }),
}));

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify([]), { status: 200 }),
  );
});

describe("TowerConsole", () => {
  it("renders the tower console", () => {
    renderWithProviders(<TowerConsole />);
    expect(screen.getByTestId("tower-console")).toBeInTheDocument();
  });

  it("shows Tower label in the header", () => {
    renderWithProviders(<TowerConsole />);
    expect(screen.getByText("Tower")).toBeInTheDocument();
  });

  it("shows Start button when idle", () => {
    renderWithProviders(<TowerConsole />);
    expect(screen.getByTestId("tower-console-start")).toBeInTheDocument();
    expect(screen.getByTestId("tower-console-start")).toHaveTextContent("Start");
  });

  it("shows goal input and project select when idle for non-terminal providers", () => {
    renderWithProviders(<TowerConsole />);
    expect(screen.getByTestId("tower-console-goal")).toBeInTheDocument();
    expect(screen.getByTestId("tower-console-project")).toBeInTheDocument();
  });

  it("shows status badge", () => {
    renderWithProviders(<TowerConsole />);
    expect(screen.getByText("idle")).toBeInTheDocument();
  });

  it("does not show terminal when idle", () => {
    renderWithProviders(<TowerConsole />);
    expect(screen.queryByTestId("tower-console-terminal")).not.toBeInTheDocument();
  });

  it("disables Start when no project is selected", () => {
    renderWithProviders(<TowerConsole />);
    expect(screen.getByTestId("tower-console-start")).toBeDisabled();
  });

  it("treats codex as a terminal-backed provider", () => {
    renderWithProviders(<TowerConsole />, {
      initialState: {
        projects: [
          {
            id: "proj-1",
            name: "Codex Project",
            description: null,
            repo_path: null,
            github_repo: null,
            agent_provider: "codex",
            status: "active",
            created_at: "2024-01-01T00:00:00Z",
            updated_at: "2024-01-01T00:00:00Z",
          },
        ],
      },
    });
    expect(screen.queryByTestId("tower-console-goal")).not.toBeInTheDocument();
  });

  it("shows a mismatch warning when Tower is attached to a different project", () => {
    renderWithProviders(<TowerConsole />, {
      initialState: {
        projects: [
          {
            id: "proj-1",
            name: "Codex Project",
            description: null,
            repo_path: null,
            github_repo: null,
            agent_provider: "codex",
            status: "active",
            created_at: "2024-01-01T00:00:00Z",
            updated_at: "2024-01-01T00:00:00Z",
          },
          {
            id: "proj-2",
            name: "Claude Project",
            description: null,
            repo_path: null,
            github_repo: null,
            agent_provider: "claude_code",
            status: "paused",
            created_at: "2024-01-01T00:00:00Z",
            updated_at: "2024-01-01T00:00:00Z",
          },
        ],
        towerDetail: {
          state: "managing",
          current_goal: null,
          current_project_id: "proj-2",
          current_session_id: "tower-1",
          leader_session_id: null,
          leader_activity_preview: null,
        },
      },
    });
    expect(screen.getByTestId("tower-console-provider-mismatch")).toBeInTheDocument();
    expect(screen.getByTestId("tower-console-restart-provider")).toHaveTextContent(
      "Apply selected project and restart Tower",
    );
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppProvider } from "../../context/AppContext";
import ProjectView, { getProjectAceSessions } from "../ProjectView";
import type { Session } from "../../types";

// Mock WebSocket
class MockWebSocket {
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readyState = 1;
  send = vi.fn();
  close = vi.fn();
}

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify([]), { status: 200 }),
  );
  vi.stubGlobal("WebSocket", MockWebSocket);
});

function renderProjectView(projectId = "test-id") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AppProvider>
        <MemoryRouter initialEntries={[`/projects/${projectId}`]}>
          <Routes>
            <Route path="/projects/:id" element={<ProjectView />} />
          </Routes>
        </MemoryRouter>
      </AppProvider>
    </QueryClientProvider>,
  );
}

function session(overrides: Partial<Session>): Session {
  return {
    id: "session-id",
    project_id: "test-id",
    session_type: "ace",
    name: "Ace",
    status: "idle",
    task_id: null,
    host: null,
    tmux_session: null,
    tmux_pane: null,
    alternate_on: false,
    auto_accept: false,
    created_at: "2026-06-08T00:00:00Z",
    updated_at: "2026-06-08T00:00:00Z",
    ...overrides,
  };
}

describe("ProjectView", () => {
  it("renders the project view", () => {
    renderProjectView();
    expect(screen.getByTestId("project-view")).toBeInTheDocument();
  });

  it("shows 'not found' when project does not exist", () => {
    renderProjectView("nonexistent");
    expect(screen.getByText("Project not found.")).toBeInTheDocument();
  });

  it("renders without tower banner (Tower is in shell Layout)", () => {
    renderProjectView();
    // TowerBanner was removed — Tower panel lives in the shell Layout now
    expect(screen.getByTestId("project-view")).toBeInTheDocument();
    expect(screen.queryByTestId("tower-banner")).not.toBeInTheDocument();
  });

  it("contains the task board section", () => {
    renderProjectView();
    // The task board renders even with empty tasks
    expect(screen.getByTestId("project-view")).toBeInTheDocument();
  });

  it("passes only Ace sessions to the Project Aces panel", () => {
    const visible = getProjectAceSessions(
      [
        session({ id: "tower", name: "tower-codex", session_type: "tower" }),
        session({ id: "leader", name: "leader", session_type: "manager" }),
        session({ id: "ace", name: "Scaffold Ace", session_type: "ace" }),
        session({ id: "other", project_id: "other-project", session_type: "ace" }),
      ],
      "test-id",
    );

    expect(visible).toHaveLength(1);
    expect(visible[0]?.name).toBe("Scaffold Ace");
  });
});

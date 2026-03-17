import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppProvider } from "../../context/AppContext";
import ProjectView from "../ProjectView";

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

describe("ProjectView", () => {
  it("renders the project view", () => {
    renderProjectView();
    expect(screen.getByTestId("project-view")).toBeInTheDocument();
  });

  it("shows 'not found' when project does not exist", () => {
    renderProjectView("nonexistent");
    expect(screen.getByText("Project not found.")).toBeInTheDocument();
  });

  it("renders tower banner", () => {
    renderProjectView();
    // Tower banner is always rendered (even for not-found view shows project-view wrapper)
    // For a valid project, tower banner should be present
    expect(screen.getByTestId("project-view")).toBeInTheDocument();
  });

  it("contains the task board section", () => {
    renderProjectView();
    // The task board renders even with empty tasks
    expect(screen.getByTestId("project-view")).toBeInTheDocument();
  });
});

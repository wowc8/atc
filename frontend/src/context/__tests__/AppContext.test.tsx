import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppProvider, useAppContext } from "../AppContext";

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
  vi.restoreAllMocks();
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify([]), { status: 200 }),
  );
  vi.stubGlobal("WebSocket", MockWebSocket);
});

function TestConsumer() {
  const { state, dispatch } = useAppContext();
  return (
    <div>
      <span data-testid="project-count">{state.projects.length}</span>
      <span data-testid="selected-project">{state.selectedProjectId ?? "none"}</span>
      <button
        data-testid="select-project"
        onClick={() => dispatch({ type: "SELECT_PROJECT", payload: "proj-1" })}
      >
        Select
      </button>
      <button
        data-testid="set-projects"
        onClick={() =>
          dispatch({
            type: "SET_PROJECTS",
            payload: [
              {
                id: "proj-1",
                name: "Test",
                description: null,
                repo_path: null,
                github_repo: null,
                status: "active",
                created_at: "2024-01-01T00:00:00Z",
                updated_at: "2024-01-01T00:00:00Z",
              },
            ],
          })
        }
      >
        Set Projects
      </button>
    </div>
  );
}

function renderWithContext(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AppProvider>
        <MemoryRouter>{ui}</MemoryRouter>
      </AppProvider>
    </QueryClientProvider>,
  );
}

describe("AppContext", () => {
  it("provides initial state", () => {
    renderWithContext(<TestConsumer />);
    expect(screen.getByTestId("project-count")).toHaveTextContent("0");
    expect(screen.getByTestId("selected-project")).toHaveTextContent("none");
  });

  it("dispatches SELECT_PROJECT", async () => {
    renderWithContext(<TestConsumer />);
    await act(async () => {
      screen.getByTestId("select-project").click();
    });
    expect(screen.getByTestId("selected-project")).toHaveTextContent("proj-1");
  });

  it("dispatches SET_PROJECTS", async () => {
    renderWithContext(<TestConsumer />);
    await act(async () => {
      screen.getByTestId("set-projects").click();
    });
    expect(screen.getByTestId("project-count")).toHaveTextContent("1");
  });

  it("throws when used outside provider", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<TestConsumer />)).toThrow(
      "useAppContext must be used within AppProvider",
    );
    consoleError.mockRestore();
  });
});

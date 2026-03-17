import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AceList from "../AceList";
import { renderWithProviders } from "../../../test/helpers";
import type { Session } from "../../../types";

// Mock useTerminal to avoid xterm.js dependencies
vi.mock("../../../hooks/useTerminal", () => ({
  useTerminal: () => ({ attachRef: vi.fn() }),
}));

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
  vi.spyOn(globalThis, "fetch").mockImplementation(() =>
    Promise.resolve(new Response(JSON.stringify([]), { status: 200 })),
  );
  vi.stubGlobal("WebSocket", MockWebSocket);
});

const baseSessions: Session[] = [
  {
    id: "sess-1",
    project_id: "proj-1",
    session_type: "ace",
    name: "alpha",
    status: "working",
    task_id: null,
    host: null,
    tmux_session: null,
    tmux_pane: null,
    alternate_on: false,
    auto_accept: true,
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
  },
  {
    id: "sess-2",
    project_id: "proj-1",
    session_type: "ace",
    name: "bravo",
    status: "idle",
    task_id: null,
    host: null,
    tmux_session: null,
    tmux_pane: null,
    alternate_on: false,
    auto_accept: true,
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
  },
];

describe("AceList", () => {
  it("renders the ace list", () => {
    renderWithProviders(
      <AceList projectId="proj-1" sessions={[]} onRefresh={vi.fn()} />,
    );
    expect(screen.getByTestId("ace-list")).toBeInTheDocument();
  });

  it("shows empty message when no sessions", () => {
    renderWithProviders(
      <AceList projectId="proj-1" sessions={[]} onRefresh={vi.fn()} />,
    );
    expect(screen.getByText("No aces yet. Create one above.")).toBeInTheDocument();
  });

  it("shows session count", () => {
    renderWithProviders(
      <AceList projectId="proj-1" sessions={baseSessions} onRefresh={vi.fn()} />,
    );
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("shows session names in tabs", () => {
    renderWithProviders(
      <AceList projectId="proj-1" sessions={baseSessions} onRefresh={vi.fn()} />,
    );
    expect(screen.getByText("alpha")).toBeInTheDocument();
    expect(screen.getByText("bravo")).toBeInTheDocument();
  });

  it("shows create input", () => {
    renderWithProviders(
      <AceList projectId="proj-1" sessions={[]} onRefresh={vi.fn()} />,
    );
    expect(screen.getByPlaceholderText("New ace name...")).toBeInTheDocument();
  });

  it("disables Add button when input is empty", () => {
    renderWithProviders(
      <AceList projectId="proj-1" sessions={[]} onRefresh={vi.fn()} />,
    );
    expect(screen.getByText("+ Add")).toBeDisabled();
  });

  it("enables Add button when name is typed", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <AceList projectId="proj-1" sessions={[]} onRefresh={vi.fn()} />,
    );
    await user.type(screen.getByPlaceholderText("New ace name..."), "charlie");
    expect(screen.getByText("+ Add")).not.toBeDisabled();
  });

  it("shows Stop button for running sessions", () => {
    renderWithProviders(
      <AceList projectId="proj-1" sessions={baseSessions} onRefresh={vi.fn()} />,
    );
    expect(screen.getAllByTitle("Stop").length).toBe(1);
  });

  it("shows Start button for idle sessions", () => {
    renderWithProviders(
      <AceList projectId="proj-1" sessions={baseSessions} onRefresh={vi.fn()} />,
    );
    expect(screen.getAllByTitle("Start").length).toBe(1);
  });

  // Compact mode tests
  it("renders compact mode", () => {
    renderWithProviders(
      <AceList projectId="proj-1" sessions={baseSessions} onRefresh={vi.fn()} compact />,
    );
    const list = screen.getByTestId("ace-list");
    expect(list).toHaveClass("ace-list--compact");
  });

  it("shows Workers heading in compact mode", () => {
    renderWithProviders(
      <AceList projectId="proj-1" sessions={baseSessions} onRefresh={vi.fn()} compact />,
    );
    expect(screen.getByText("Workers")).toBeInTheDocument();
  });

  it("shows Aces heading in default mode", () => {
    renderWithProviders(
      <AceList projectId="proj-1" sessions={baseSessions} onRefresh={vi.fn()} />,
    );
    expect(screen.getByText("Aces")).toBeInTheDocument();
  });

  it("shows mini cards in compact mode", () => {
    renderWithProviders(
      <AceList projectId="proj-1" sessions={baseSessions} onRefresh={vi.fn()} compact />,
    );
    expect(screen.getByText("alpha")).toBeInTheDocument();
    expect(screen.getByText("bravo")).toBeInTheDocument();
  });

  it("shows empty message in compact mode", () => {
    renderWithProviders(
      <AceList projectId="proj-1" sessions={[]} onRefresh={vi.fn()} compact />,
    );
    expect(screen.getByText("No aces yet.")).toBeInTheDocument();
  });

  it("shows create form in compact mode", () => {
    renderWithProviders(
      <AceList projectId="proj-1" sessions={[]} onRefresh={vi.fn()} compact />,
    );
    expect(screen.getByPlaceholderText("New ace name...")).toBeInTheDocument();
  });

  it("shows session count in compact mode", () => {
    renderWithProviders(
      <AceList projectId="proj-1" sessions={baseSessions} onRefresh={vi.fn()} compact />,
    );
    expect(screen.getByText("2")).toBeInTheDocument();
  });
});

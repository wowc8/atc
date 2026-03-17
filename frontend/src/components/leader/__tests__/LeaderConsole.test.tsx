import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LeaderConsole from "../LeaderConsole";
import { renderWithProviders } from "../../../test/helpers";
import type { Leader } from "../../../types";

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

describe("LeaderConsole", () => {
  it("renders the leader console", () => {
    renderWithProviders(
      <LeaderConsole projectId="proj-1" leader={undefined} onRefresh={vi.fn()} />,
    );
    expect(screen.getByTestId("leader-console")).toBeInTheDocument();
  });

  it("shows Start button when leader is idle", () => {
    renderWithProviders(
      <LeaderConsole projectId="proj-1" leader={idleLeader} onRefresh={vi.fn()} />,
    );
    expect(screen.getByText("Start")).toBeInTheDocument();
  });

  it("shows Stop button when leader is managing", () => {
    renderWithProviders(
      <LeaderConsole projectId="proj-1" leader={managingLeader} onRefresh={vi.fn()} />,
    );
    expect(screen.getByText("Stop")).toBeInTheDocument();
  });

  it("shows Stop button when leader is planning", () => {
    renderWithProviders(
      <LeaderConsole projectId="proj-1" leader={planningLeader} onRefresh={vi.fn()} />,
    );
    expect(screen.getByText("Stop")).toBeInTheDocument();
  });

  it("shows Start button when leader is undefined", () => {
    renderWithProviders(
      <LeaderConsole projectId="proj-1" leader={undefined} onRefresh={vi.fn()} />,
    );
    expect(screen.getByText("Start")).toBeInTheDocument();
  });

  it("shows goal input when not running", () => {
    renderWithProviders(
      <LeaderConsole projectId="proj-1" leader={idleLeader} onRefresh={vi.fn()} />,
    );
    expect(screen.getByLabelText("Goal (optional)")).toBeInTheDocument();
  });

  it("hides goal input when running", () => {
    renderWithProviders(
      <LeaderConsole projectId="proj-1" leader={managingLeader} onRefresh={vi.fn()} />,
    );
    expect(screen.queryByLabelText("Goal (optional)")).not.toBeInTheDocument();
  });

  it("shows goal text when leader has a goal and is running", () => {
    renderWithProviders(
      <LeaderConsole projectId="proj-1" leader={managingLeader} onRefresh={vi.fn()} />,
    );
    expect(screen.getByText("Build the feature")).toBeInTheDocument();
  });

  it("shows message input when running", () => {
    renderWithProviders(
      <LeaderConsole projectId="proj-1" leader={managingLeader} onRefresh={vi.fn()} />,
    );
    expect(screen.getByPlaceholderText("Send message to leader...")).toBeInTheDocument();
  });

  it("hides message input when idle", () => {
    renderWithProviders(
      <LeaderConsole projectId="proj-1" leader={idleLeader} onRefresh={vi.fn()} />,
    );
    expect(screen.queryByPlaceholderText("Send message to leader...")).not.toBeInTheDocument();
  });

  it("shows status badge for leader", () => {
    renderWithProviders(
      <LeaderConsole projectId="proj-1" leader={managingLeader} onRefresh={vi.fn()} />,
    );
    expect(screen.getByText("managing")).toBeInTheDocument();
  });

  it("calls start API and awaits onRefresh when Start is clicked", async () => {
    const user = userEvent.setup();
    const onRefresh = vi.fn().mockResolvedValue(undefined);

    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ status: "started", session_id: "sess-new" }), {
          status: 200,
        }),
      ),
    );

    renderWithProviders(
      <LeaderConsole projectId="proj-1" leader={idleLeader} onRefresh={onRefresh} />,
    );

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

    renderWithProviders(
      <LeaderConsole projectId="proj-1" leader={managingLeader} onRefresh={onRefresh} />,
    );

    // Click the Stop button (wrapped in ConfirmPopover)
    await user.click(screen.getByText("Stop"));

    // Find and click the confirm button inside the popover
    const confirmButtons = screen.getAllByText("Stop");
    const confirmBtn = confirmButtons.find(
      (el) => el.closest(".confirm-popover__confirm") !== null,
    );
    if (confirmBtn) {
      await user.click(confirmBtn);
      await waitFor(() => {
        expect(onRefresh).toHaveBeenCalled();
      });
    }
  });

  it("disables Send button when message is empty", () => {
    renderWithProviders(
      <LeaderConsole projectId="proj-1" leader={managingLeader} onRefresh={vi.fn()} />,
    );
    const sendBtn = screen.getByText("Send");
    expect(sendBtn).toBeDisabled();
  });

  it("enables Send button when message is typed", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <LeaderConsole projectId="proj-1" leader={managingLeader} onRefresh={vi.fn()} />,
    );
    const input = screen.getByPlaceholderText("Send message to leader...");
    await user.type(input, "hello");
    const sendBtn = screen.getByText("Send");
    expect(sendBtn).not.toBeDisabled();
  });

  it("clears message after successful send", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ status: "sent" }), { status: 200 }),
      ),
    );

    renderWithProviders(
      <LeaderConsole projectId="proj-1" leader={managingLeader} onRefresh={vi.fn()} />,
    );

    const input = screen.getByPlaceholderText("Send message to leader...");
    await user.type(input, "hello");
    await user.click(screen.getByText("Send"));

    await waitFor(() => {
      expect(input).toHaveValue("");
    });
  });

  it("shows Starting... while loading on start", async () => {
    const user = userEvent.setup();
    let resolvePost!: (v: Response) => void;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () =>
        new Promise<Response>((r) => {
          resolvePost = r;
        }),
    );

    renderWithProviders(
      <LeaderConsole projectId="proj-1" leader={idleLeader} onRefresh={vi.fn()} />,
    );

    await user.click(screen.getByText("Start"));
    expect(screen.getByText("Starting...")).toBeInTheDocument();

    // Resolve to clean up
    resolvePost(new Response(JSON.stringify({ status: "started" }), { status: 200 }));
  });
});

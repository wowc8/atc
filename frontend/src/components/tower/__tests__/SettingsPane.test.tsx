import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SettingsPane from "../SettingsPane";
import { renderWithProviders } from "../../../test/helpers";

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("SettingsPane", () => {
  it("shows provider action controls for existing projects", async () => {
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
            {
              name: "claude_code",
              supports_streaming: true,
              supports_tool_use: true,
              context_window: 200000,
              model: "claude",
            },
            {
              name: "codex",
              supports_streaming: true,
              supports_tool_use: true,
              context_window: 200000,
              model: "codex",
            },
          ]),
          { status: 200 },
        ),
      );

    renderWithProviders(<SettingsPane onClose={() => undefined} />, {
      initialState: {
        projects: [
          {
            id: "proj-1",
            name: "Alpha",
            description: null,
            repo_path: null,
            github_repo: null,
            agent_provider: "claude_code",
            status: "active",
            created_at: "2024-01-01T00:00:00Z",
            updated_at: "2024-01-01T00:00:00Z",
          },
        ],
      },
    });

    await waitFor(() => {
      expect(screen.getByTestId("provider-action-project")).toBeInTheDocument();
    });
    expect(screen.getByTestId("provider-apply-project")).toBeInTheDocument();
    expect(screen.getByTestId("provider-restart-tower")).toBeInTheDocument();
    expect(screen.getByTestId("provider-action-status")).toBeInTheDocument();
  });

  it("applies the default provider to the selected project", async () => {
    const user = userEvent.setup();
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
            {
              name: "claude_code",
              supports_streaming: true,
              supports_tool_use: true,
              context_window: 200000,
              model: "claude",
            },
            {
              name: "codex",
              supports_streaming: true,
              supports_tool_use: true,
              context_window: 200000,
              model: "codex",
            },
          ]),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            id: "proj-1",
            name: "Alpha",
            description: null,
            repo_path: null,
            github_repo: null,
            agent_provider: "codex",
            status: "active",
            created_at: "2024-01-01T00:00:00Z",
            updated_at: "2024-01-01T00:00:01Z",
          }),
          { status: 200 },
        ),
      );

    renderWithProviders(<SettingsPane onClose={() => undefined} />, {
      initialState: {
        projects: [
          {
            id: "proj-1",
            name: "Alpha",
            description: null,
            repo_path: null,
            github_repo: null,
            agent_provider: "claude_code",
            status: "active",
            created_at: "2024-01-01T00:00:00Z",
            updated_at: "2024-01-01T00:00:00Z",
          },
        ],
      },
    });

    await waitFor(() => {
      expect(screen.getByTestId("provider-apply-project")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("provider-apply-project"));

    await waitFor(() => {
      expect(screen.getByText("Project provider updated.")).toBeInTheDocument();
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8420/api/projects/proj-1/agent-provider",
      expect.objectContaining({ method: "PATCH" }),
    );
  });

  it("disables apply when the selected project already matches the default provider", async () => {
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
            {
              name: "claude_code",
              supports_streaming: true,
              supports_tool_use: true,
              context_window: 200000,
              model: "claude",
            },
            {
              name: "codex",
              supports_streaming: true,
              supports_tool_use: true,
              context_window: 200000,
              model: "codex",
            },
          ]),
          { status: 200 },
        ),
      );

    renderWithProviders(<SettingsPane onClose={() => undefined} />, {
      initialState: {
        projects: [
          {
            id: "proj-1",
            name: "Alpha",
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

    await waitFor(() => {
      expect(screen.getByTestId("provider-apply-project")).toBeDisabled();
    });
    expect(screen.getByTestId("provider-apply-project")).toHaveTextContent(
      "Project already matches default",
    );
  });
});

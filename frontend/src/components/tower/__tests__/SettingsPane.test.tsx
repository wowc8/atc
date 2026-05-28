import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SettingsPane from "../SettingsPane";
import { renderWithProviders } from "../../../test/helpers";

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("SettingsPane", () => {
  it("shows global provider status instead of project apply controls", async () => {
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
            default: "codex",
            opencode_url: "http://localhost:4096",
            tmux_session: "atc",
            claude_command: "claude",
            codex_command: "codex",
          }),
          { status: 200 },
        ),
      );

    renderWithProviders(<SettingsPane onClose={() => undefined} />);

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
      "http://127.0.0.1:8420/api/settings/agent-provider",
      expect.objectContaining({ method: "PUT" }),
    );
  });
});

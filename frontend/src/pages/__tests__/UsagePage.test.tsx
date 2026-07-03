import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import UsagePage from "../UsagePage";
import { renderWithProviders } from "../../test/helpers";

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify([]), { status: 200 }),
  );
});

describe("UsagePage", () => {
  it("renders the usage page", () => {
    renderWithProviders(<UsagePage />);
    expect(screen.getByTestId("usage-page")).toBeInTheDocument();
  });

  it("shows the Usage heading", () => {
    renderWithProviders(<UsagePage />);
    expect(screen.getByText("Usage")).toBeInTheDocument();
  });

  it("shows token overview card", () => {
    renderWithProviders(<UsagePage />);
    expect(screen.getByText("Token Usage")).toBeInTheDocument();
  });

  it("shows token usage card", () => {
    renderWithProviders(<UsagePage />);
    expect(screen.getByText("Token Usage")).toBeInTheDocument();
  });

  it("shows budget utilization section", () => {
    renderWithProviders(<UsagePage />);
    expect(screen.getByText("Budget Utilization")).toBeInTheDocument();
  });

  it("shows Codex token sync status and can trigger manual sync", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation((input) => {
        const url = String(input);
        if (url.includes("/usage/tokens/sync-codex/status")) {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                enabled: true,
                running: true,
                sessions_glob: "~/.codex/sessions/**/*.jsonl",
                poll_interval_seconds: 30,
                last_started_at: null,
                last_finished_at: null,
                last_inserted_events: 0,
                last_discovered_files: 4,
                last_error: null,
              }),
              { status: 200 },
            ),
          );
        }
        if (url.includes("/usage/tokens/sync-codex")) {
          return Promise.resolve(
            new Response(
              JSON.stringify({ inserted_events: 2, enabled: true }),
              {
                status: 200,
              },
            ),
          );
        }
        return Promise.resolve(
          new Response(JSON.stringify([]), { status: 200 }),
        );
      });

    renderWithProviders(<UsagePage />);

    expect(screen.getByText("Codex Token Sync")).toBeInTheDocument();
    await screen.findByText("~/.codex/sessions/**/*.jsonl");
    fireEvent.click(screen.getByRole("button", { name: "Sync now" }));

    await waitFor(() => {
      expect(screen.getByText("Inserted 2 token events.")).toBeInTheDocument();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/usage/tokens/sync-codex",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("shows chart placeholders", () => {
    renderWithProviders(<UsagePage />);
    expect(screen.getByText(/No token data/)).toBeInTheDocument();
    expect(screen.getByText(/No resource data yet/)).toBeInTheDocument();
  });
});

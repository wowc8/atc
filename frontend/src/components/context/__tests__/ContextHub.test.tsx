import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ContextHub from "../ContextHub";
import { renderWithProviders } from "../../../test/helpers";
import type { ContextEntry } from "../../../types";

const MOCK_ENTRIES: ContextEntry[] = [
  {
    id: "e1",
    scope: "global",
    project_id: null,
    session_id: null,
    key: "coding-standards",
    entry_type: "text",
    value: "Follow TypeScript strict mode",
    restricted: false,
    position: 0,
    updated_by: "admin",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "e2",
    scope: "global",
    project_id: null,
    session_id: null,
    key: "internal-hooks",
    entry_type: "json",
    value: '{"pre_deploy": "lint"}',
    restricted: true,
    position: 1,
    updated_by: "system",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

function mockFetch(data: unknown = MOCK_ENTRIES, status = 200) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(data), { status }),
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("ContextHub", () => {
  it("renders the component", async () => {
    mockFetch();
    renderWithProviders(<ContextHub scope="global" />);
    expect(screen.getByTestId("context-hub")).toBeInTheDocument();
  });

  it("shows loading then entries", async () => {
    mockFetch();
    renderWithProviders(<ContextHub scope="global" />);
    expect(screen.getByText("Loading...")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("coding-standards")).toBeInTheDocument();
    });
  });

  it("shows entries with type badges", async () => {
    mockFetch();
    renderWithProviders(<ContextHub scope="global" />);
    await waitFor(() => {
      expect(screen.getByText("coding-standards")).toBeInTheDocument();
    });
    expect(screen.getByText("text")).toBeInTheDocument();
    expect(screen.getByText("json")).toBeInTheDocument();
  });

  it("shows restricted badge for restricted entries", async () => {
    mockFetch();
    renderWithProviders(<ContextHub scope="global" />);
    await waitFor(() => {
      expect(screen.getByText("restricted")).toBeInTheDocument();
    });
  });

  it("shows empty state when no entries", async () => {
    mockFetch([]);
    renderWithProviders(<ContextHub scope="global" />);
    await waitFor(() => {
      expect(screen.getByText("No context entries yet.")).toBeInTheDocument();
    });
  });

  it("expands entry on click to show edit form", async () => {
    mockFetch();
    const user = userEvent.setup();
    renderWithProviders(<ContextHub scope="global" />);
    await waitFor(() => {
      expect(screen.getByText("coding-standards")).toBeInTheDocument();
    });
    await user.click(screen.getByText("coding-standards"));
    expect(screen.getByTestId("context-hub-entry-value")).toBeInTheDocument();
  });

  it("shows scope tabs when showScopeTabs is true", async () => {
    mockFetch();
    renderWithProviders(
      <ContextHub scope="global" showScopeTabs availableScopes={["global", "project"]} />,
    );
    expect(screen.getByTestId("context-hub-tabs")).toBeInTheDocument();
    expect(screen.getByText("Global")).toBeInTheDocument();
    expect(screen.getByText("Project")).toBeInTheDocument();
  });

  it("toggles create form", async () => {
    mockFetch();
    const user = userEvent.setup();
    renderWithProviders(<ContextHub scope="global" />);
    await waitFor(() => {
      expect(screen.getByTestId("context-hub-create-btn")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("context-hub-create-btn"));
    expect(screen.getByTestId("context-hub-create-form")).toBeInTheDocument();
  });

  it("shows error on fetch failure", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("Server error", { status: 500 }),
    );
    renderWithProviders(<ContextHub scope="global" />);
    await waitFor(() => {
      expect(screen.getByTestId("context-hub-error")).toBeInTheDocument();
    });
  });

  it("calls correct API path for project scope", async () => {
    const fetchSpy = mockFetch();
    renderWithProviders(<ContextHub scope="project" projectId="proj-1" />);
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining("/projects/proj-1/context"),
        expect.anything(),
      );
    });
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
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

  it("shows goal input and project select when idle", () => {
    renderWithProviders(<TowerConsole />);
    expect(screen.getByTestId("tower-console-goal")).toBeInTheDocument();
    expect(screen.getByTestId("tower-console-project")).toBeInTheDocument();
  });

  it("shows status badge", () => {
    renderWithProviders(<TowerConsole />);
    // The status badge should render with the brain status
    expect(screen.getByText("idle")).toBeInTheDocument();
  });

  it("does not show terminal when idle", () => {
    renderWithProviders(<TowerConsole />);
    expect(screen.queryByTestId("tower-console-terminal")).not.toBeInTheDocument();
  });

  it("disables Start when no project is selected", () => {
    renderWithProviders(<TowerConsole />);
    // No projects available, so no project is selected
    expect(screen.getByTestId("tower-console-start")).toBeDisabled();
  });
});

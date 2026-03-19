import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TowerPanel from "../TowerPanel";
import { renderWithProviders } from "../../../test/helpers";

// Mock useTerminal since it requires WebSocket/xterm
vi.mock("../../../hooks/useTerminal", () => ({
  useTerminal: () => ({
    attachRef: vi.fn(),
    fit: vi.fn(),
    sendInput: vi.fn(),
  }),
}));

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify([]), { status: 200 }),
  );
});

describe("TowerPanel", () => {
  it("renders the tower panel", () => {
    renderWithProviders(<TowerPanel />);
    expect(screen.getByTestId("tower-panel")).toBeInTheDocument();
  });

  it("shows Tower label in the bar", () => {
    renderWithProviders(<TowerPanel />);
    expect(screen.getByText("Tower")).toBeInTheDocument();
  });

  it("starts minimized", () => {
    renderWithProviders(<TowerPanel />);
    expect(screen.getByTestId("tower-panel-content")).not.toBeVisible();
  });

  it("expands when toggle is clicked", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TowerPanel />);

    await user.click(screen.getByTestId("tower-panel-toggle"));
    expect(screen.getByTestId("tower-panel-content")).toBeVisible();
  });

  it("shows Start button in the bar when idle", () => {
    renderWithProviders(<TowerPanel />);
    expect(screen.getByTestId("tower-panel-start")).toBeInTheDocument();
  });

  it("does not show a project dropdown", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TowerPanel />);

    await user.click(screen.getByTestId("tower-panel-toggle"));
    expect(screen.queryByTestId("tower-panel-project")).not.toBeInTheDocument();
  });

  it("shows context label in the bar", () => {
    renderWithProviders(<TowerPanel />);
    expect(screen.getByTestId("tower-panel-context")).toBeInTheDocument();
  });

  it("shows Idle in ticker when no goal is set", () => {
    renderWithProviders(<TowerPanel />);
    expect(screen.getByText("Idle")).toBeInTheDocument();
  });

  it("disables Start button when no project is active", () => {
    renderWithProviders(<TowerPanel />);
    const startBtn = screen.getByTestId("tower-panel-start");
    expect(startBtn).toBeDisabled();
  });

  it("collapses when toggle is clicked again", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TowerPanel />);

    // Expand
    await user.click(screen.getByTestId("tower-panel-toggle"));
    expect(screen.getByTestId("tower-panel-content")).toBeVisible();

    // Collapse — content stays in DOM but is hidden
    await user.click(screen.getByTestId("tower-panel-toggle"));
    expect(screen.getByTestId("tower-panel-content")).not.toBeVisible();
  });
});

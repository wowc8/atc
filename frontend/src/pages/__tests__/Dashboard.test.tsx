import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import Dashboard from "../Dashboard";
import { renderWithProviders } from "../../test/helpers";

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify([]), { status: 200 }),
  );
});

describe("Dashboard", () => {
  it("renders the dashboard page", () => {
    renderWithProviders(<Dashboard />);
    expect(screen.getByTestId("dashboard-page")).toBeInTheDocument();
  });

  it("shows the dashboard heading", () => {
    renderWithProviders(<Dashboard />);
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
  });

  it("shows cost summary cards", () => {
    renderWithProviders(<Dashboard />);
    expect(screen.getByText("Cost")).toBeInTheDocument();
    expect(screen.getByText("Tokens")).toBeInTheDocument();
    expect(screen.getByText("Sessions")).toBeInTheDocument();
    expect(screen.getByText("Notifications")).toBeInTheDocument();
  });

  it("shows empty state for projects", () => {
    renderWithProviders(<Dashboard />);
    expect(
      screen.getByText("No active projects."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Create your first project"),
    ).toBeInTheDocument();
  });

  it("shows the Projects heading", () => {
    renderWithProviders(<Dashboard />);
    expect(screen.getByText("Projects")).toBeInTheDocument();
  });

  it("does not render TowerConsole inline (Tower is now persistent in layout)", () => {
    renderWithProviders(<Dashboard />);
    expect(screen.queryByTestId("tower-console")).not.toBeInTheDocument();
  });
});

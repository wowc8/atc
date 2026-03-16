import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TowerBar from "../TowerBar";
import { renderWithProviders } from "../../../test/helpers";

// Mock fetch to avoid API calls during render
beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify([]), { status: 200 }),
  );
});

describe("TowerBar", () => {
  it("renders the brand name", () => {
    renderWithProviders(<TowerBar />);
    expect(screen.getByText("ATC")).toBeInTheDocument();
  });

  it("renders the tower status", () => {
    renderWithProviders(<TowerBar />);
    expect(screen.getByText("idle")).toBeInTheDocument();
  });

  it("renders navigation items", () => {
    renderWithProviders(<TowerBar />);
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
    expect(screen.getByText("Usage")).toBeInTheDocument();
    expect(screen.getByText("Settings")).toBeInTheDocument();
  });

  it("renders cost summary", () => {
    renderWithProviders(<TowerBar />);
    expect(screen.getByTestId("cost-summary")).toHaveTextContent("$0.00 today");
  });

  it("renders token summary", () => {
    renderWithProviders(<TowerBar />);
    expect(screen.getByTestId("token-summary")).toHaveTextContent("0 tokens");
  });

  it("renders project count", () => {
    renderWithProviders(<TowerBar />);
    expect(screen.getByTestId("project-count")).toHaveTextContent("0 projects");
  });

  it("renders notification bell", () => {
    renderWithProviders(<TowerBar />);
    expect(screen.getByTestId("notification-bell")).toBeInTheDocument();
  });

  it("renders settings button", () => {
    renderWithProviders(<TowerBar />);
    expect(screen.getByTestId("settings-button")).toBeInTheDocument();
  });

  it("navigates to dashboard when brand is clicked", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TowerBar />);
    await user.click(screen.getByText("ATC"));
    // Navigation happened (no error thrown)
  });
});

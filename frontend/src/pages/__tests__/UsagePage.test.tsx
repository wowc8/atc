import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
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

  it("shows cost overview card", () => {
    renderWithProviders(<UsagePage />);
    expect(screen.getByText("Cost Overview")).toBeInTheDocument();
  });

  it("shows token usage card", () => {
    renderWithProviders(<UsagePage />);
    expect(screen.getByText("Token Usage")).toBeInTheDocument();
  });

  it("shows budget utilization section", () => {
    renderWithProviders(<UsagePage />);
    expect(screen.getByText("Budget Utilization")).toBeInTheDocument();
  });

  it("shows chart placeholders", () => {
    renderWithProviders(<UsagePage />);
    expect(
      screen.getByText(/Cost chart placeholder/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Token chart placeholder/),
    ).toBeInTheDocument();
  });
});

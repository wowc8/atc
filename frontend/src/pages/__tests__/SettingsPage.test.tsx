import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import SettingsPage from "../SettingsPage";
import { renderWithProviders } from "../../test/helpers";

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify([]), { status: 200 }),
  );
});

describe("SettingsPage", () => {
  it("renders the settings page", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByTestId("settings-page")).toBeInTheDocument();
  });

  it("shows the Settings heading", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByText("Settings")).toBeInTheDocument();
  });

  it("shows connection section", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByText("Connection")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
  });

  it("shows tower status section", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByText("Tower Status")).toBeInTheDocument();
  });

  it("shows appearance section", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByText("Appearance")).toBeInTheDocument();
    expect(screen.getByText("Dark")).toBeInTheDocument();
  });

  it("shows the backend URL", () => {
    renderWithProviders(<SettingsPage />);
    const input = screen.getByLabelText("Backend URL") as HTMLInputElement;
    expect(input.value).toBe("http://127.0.0.1:8420");
  });
});

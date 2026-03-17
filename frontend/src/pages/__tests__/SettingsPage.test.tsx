import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import SettingsPage from "../SettingsPage";
import { renderWithProviders } from "../../test/helpers";

beforeEach(() => {
  localStorage.clear();
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

  it("shows GitHub Defaults section", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByText("GitHub Defaults")).toBeInTheDocument();
    expect(screen.getByLabelText("Default Org / Username")).toBeInTheDocument();
  });

  it("persists GitHub org to localStorage", () => {
    renderWithProviders(<SettingsPage />);
    const input = screen.getByLabelText("Default Org / Username") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "my-org" } });
    expect(localStorage.getItem("atc:github_default_org")).toBe("my-org");
  });

  it("clears localStorage when GitHub org is emptied", () => {
    localStorage.setItem("atc:github_default_org", "old-org");
    renderWithProviders(<SettingsPage />);
    const input = screen.getByLabelText("Default Org / Username") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "" } });
    expect(localStorage.getItem("atc:github_default_org")).toBeNull();
  });

  it("shows export section", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByTestId("export-section")).toBeInTheDocument();
    expect(screen.getByText("Export")).toBeInTheDocument();
  });

  it("shows export all button", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByTestId("export-all-btn")).toBeInTheDocument();
  });

  it("shows import section", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByTestId("import-section")).toBeInTheDocument();
    expect(screen.getByText("Import")).toBeInTheDocument();
  });

  it("shows import project button", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByTestId("import-project-btn")).toBeInTheDocument();
  });

  it("shows import all button", () => {
    renderWithProviders(<SettingsPage />);
    expect(screen.getByTestId("import-all-btn")).toBeInTheDocument();
  });
});

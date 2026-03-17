import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TowerBanner from "../TowerBanner";
import { renderWithProviders } from "../../../test/helpers";
import type { TowerStatus } from "../../../types";

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify([]), { status: 200 }),
  );
});

const idleStatus: TowerStatus = {
  status: "idle",
  message: "",
  active_projects: 0,
};

const activeStatus: TowerStatus = {
  status: "planning",
  message: "Creating Leader for project Alpha",
  active_projects: 2,
};

describe("TowerBanner", () => {
  it("renders the tower banner", () => {
    renderWithProviders(<TowerBanner towerStatus={idleStatus} />);
    expect(screen.getByTestId("tower-banner")).toBeInTheDocument();
  });

  it("shows Tower label", () => {
    renderWithProviders(<TowerBanner towerStatus={idleStatus} />);
    expect(screen.getByText("Tower")).toBeInTheDocument();
  });

  it("shows status text", () => {
    renderWithProviders(<TowerBanner towerStatus={activeStatus} />);
    expect(screen.getByText("planning")).toBeInTheDocument();
  });

  it("shows activity ticker with message", () => {
    renderWithProviders(<TowerBanner towerStatus={activeStatus} />);
    expect(
      screen.getByText("Creating Leader for project Alpha"),
    ).toBeInTheDocument();
  });

  it("shows Idle in ticker when no message", () => {
    renderWithProviders(<TowerBanner towerStatus={idleStatus} />);
    expect(screen.getByText("Idle")).toBeInTheDocument();
  });

  it("starts minimized by default", () => {
    renderWithProviders(<TowerBanner towerStatus={idleStatus} />);
    expect(screen.queryByTestId("tower-banner-detail")).not.toBeInTheDocument();
  });

  it("expands when toggle is clicked", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TowerBanner towerStatus={activeStatus} />);

    await user.click(screen.getByLabelText("Expand Tower"));
    expect(screen.getByTestId("tower-banner-detail")).toBeInTheDocument();
  });

  it("shows detail content when expanded", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TowerBanner towerStatus={activeStatus} />);

    await user.click(screen.getByLabelText("Expand Tower"));
    expect(screen.getByText("2 active projects")).toBeInTheDocument();
  });

  it("shows singular project text for 1 project", async () => {
    const user = userEvent.setup();
    const oneProject: TowerStatus = {
      status: "planning",
      message: "Working",
      active_projects: 1,
    };
    renderWithProviders(<TowerBanner towerStatus={oneProject} />);

    await user.click(screen.getByLabelText("Expand Tower"));
    expect(screen.getByText("1 active project")).toBeInTheDocument();
  });

  it("minimizes when toggle is clicked again", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TowerBanner towerStatus={activeStatus} />);

    // Expand
    await user.click(screen.getByLabelText("Expand Tower"));
    expect(screen.getByTestId("tower-banner-detail")).toBeInTheDocument();

    // Minimize
    await user.click(screen.getByLabelText("Minimize Tower"));
    expect(screen.queryByTestId("tower-banner-detail")).not.toBeInTheDocument();
  });

  it("shows no activity message when expanded with no message", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TowerBanner towerStatus={idleStatus} />);

    await user.click(screen.getByLabelText("Expand Tower"));
    expect(screen.getByText("No current activity.")).toBeInTheDocument();
  });

  it("applies minimized class when minimized", () => {
    renderWithProviders(<TowerBanner towerStatus={idleStatus} />);
    const banner = screen.getByTestId("tower-banner");
    expect(banner).toHaveClass("tower-banner--minimized");
  });

  it("removes minimized class when expanded", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TowerBanner towerStatus={idleStatus} />);

    await user.click(screen.getByLabelText("Expand Tower"));
    const banner = screen.getByTestId("tower-banner");
    expect(banner).not.toHaveClass("tower-banner--minimized");
  });
});

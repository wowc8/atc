import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import StatusBadge from "../StatusBadge";

describe("StatusBadge", () => {
  it("renders the status label", () => {
    render(<StatusBadge status="active" />);
    expect(screen.getByTestId("status-badge")).toHaveTextContent("active");
  });

  it("replaces underscores with spaces", () => {
    render(<StatusBadge status="in_progress" />);
    expect(screen.getByTestId("status-badge")).toHaveTextContent("in progress");
  });

  it("renders a colored dot", () => {
    const { container } = render(<StatusBadge status="active" />);
    const dot = container.querySelector(".status-badge__dot");
    expect(dot).toBeTruthy();
    expect(dot?.getAttribute("style")).toContain("background");
  });

  it("supports sm size", () => {
    render(<StatusBadge status="idle" size="sm" />);
    const badge = screen.getByTestId("status-badge");
    expect(badge.className).toContain("status-badge--sm");
  });

  it("defaults to md size", () => {
    render(<StatusBadge status="idle" />);
    const badge = screen.getByTestId("status-badge");
    expect(badge.className).toContain("status-badge--md");
  });

  it("handles unknown status gracefully", () => {
    render(<StatusBadge status="unknown_status" />);
    expect(screen.getByTestId("status-badge")).toHaveTextContent("unknown status");
  });
});

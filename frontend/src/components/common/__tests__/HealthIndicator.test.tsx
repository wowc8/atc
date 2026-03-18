import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import HealthIndicator from "../HealthIndicator";

describe("HealthIndicator", () => {
  it("renders nothing when health is undefined", () => {
    const { container } = render(<HealthIndicator health={undefined} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders a dot for alive health", () => {
    render(<HealthIndicator health="alive" />);
    const indicator = screen.getByTestId("health-indicator");
    expect(indicator).toBeTruthy();
    const dot = indicator.querySelector(".health-indicator__dot");
    expect(dot?.className).toContain("health-indicator__dot--pulse");
  });

  it("renders without pulse for stale health", () => {
    render(<HealthIndicator health="stale" />);
    const indicator = screen.getByTestId("health-indicator");
    const dot = indicator.querySelector(".health-indicator__dot");
    expect(dot?.className).not.toContain("health-indicator__dot--pulse");
  });

  it("renders without pulse for stopped health", () => {
    render(<HealthIndicator health="stopped" />);
    const indicator = screen.getByTestId("health-indicator");
    const dot = indicator.querySelector(".health-indicator__dot");
    expect(dot?.className).not.toContain("health-indicator__dot--pulse");
  });

  it("supports sm size", () => {
    render(<HealthIndicator health="alive" size="sm" />);
    const indicator = screen.getByTestId("health-indicator");
    expect(indicator.className).toContain("health-indicator--sm");
  });

  it("supports md size", () => {
    render(<HealthIndicator health="alive" size="md" />);
    const indicator = screen.getByTestId("health-indicator");
    expect(indicator.className).toContain("health-indicator--md");
  });

  it("has title attribute with health info", () => {
    render(<HealthIndicator health="stale" />);
    const indicator = screen.getByTestId("health-indicator");
    expect(indicator.getAttribute("title")).toBe("Heartbeat: stale");
  });
});

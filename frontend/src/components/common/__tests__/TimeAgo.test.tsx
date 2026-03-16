import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import TimeAgo from "../TimeAgo";

describe("TimeAgo", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders a time element with datetime attribute", () => {
    const datetime = new Date().toISOString();
    render(<TimeAgo datetime={datetime} />);
    const el = screen.getByTestId("time-ago");
    expect(el.tagName).toBe("TIME");
    expect(el).toHaveAttribute("datetime", datetime);
  });

  it("shows relative time", () => {
    const fiveMinAgo = new Date(Date.now() - 5 * 60_000).toISOString();
    render(<TimeAgo datetime={fiveMinAgo} />);
    expect(screen.getByTestId("time-ago")).toHaveTextContent("5m ago");
  });

  it("shows 'just now' for very recent times", () => {
    const now = new Date().toISOString();
    render(<TimeAgo datetime={now} />);
    expect(screen.getByTestId("time-ago")).toHaveTextContent("just now");
  });

  it("has a title with the full date", () => {
    const datetime = "2024-03-15T10:30:00Z";
    render(<TimeAgo datetime={datetime} />);
    const el = screen.getByTestId("time-ago");
    expect(el.getAttribute("title")).toBeTruthy();
  });

  it("updates display periodically", () => {
    vi.useFakeTimers();
    const oneMinAgo = new Date(Date.now() - 60_000).toISOString();
    render(<TimeAgo datetime={oneMinAgo} refreshMs={1000} />);
    expect(screen.getByTestId("time-ago")).toHaveTextContent("1m ago");
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    // After 60s, should now show 2m ago
    expect(screen.getByTestId("time-ago")).toHaveTextContent("2m ago");
    vi.useRealTimers();
  });
});

import { describe, it, expect } from "vitest";
import { parseLocalDate, parseDate, timeAgo } from "../dates";

describe("parseLocalDate", () => {
  it("parses a date-only string as local midnight", () => {
    const d = parseLocalDate("2024-03-15");
    expect(d.getFullYear()).toBe(2024);
    expect(d.getMonth()).toBe(2); // March = 2
    expect(d.getDate()).toBe(15);
    expect(d.getHours()).toBe(0);
  });

  it("throws on invalid input", () => {
    expect(() => parseLocalDate("invalid")).toThrow("Invalid date string");
  });
});

describe("parseDate", () => {
  it("parses an ISO-8601 datetime string", () => {
    const d = parseDate("2024-03-15T10:30:00Z");
    expect(d.getTime()).toBe(new Date("2024-03-15T10:30:00Z").getTime());
  });

  it("throws on invalid input", () => {
    expect(() => parseDate("not-a-date")).toThrow("Invalid datetime string");
  });
});

describe("timeAgo", () => {
  it("returns 'just now' for recent times", () => {
    const now = new Date().toISOString();
    expect(timeAgo(now)).toBe("just now");
  });

  it("returns seconds ago", () => {
    const d = new Date(Date.now() - 30_000).toISOString();
    expect(timeAgo(d)).toBe("30s ago");
  });

  it("returns minutes ago", () => {
    const d = new Date(Date.now() - 5 * 60_000).toISOString();
    expect(timeAgo(d)).toBe("5m ago");
  });

  it("returns hours ago", () => {
    const d = new Date(Date.now() - 3 * 3600_000).toISOString();
    expect(timeAgo(d)).toBe("3h ago");
  });

  it("returns days ago", () => {
    const d = new Date(Date.now() - 7 * 86400_000).toISOString();
    expect(timeAgo(d)).toBe("7d ago");
  });

  it("returns months ago", () => {
    const d = new Date(Date.now() - 60 * 86400_000).toISOString();
    expect(timeAgo(d)).toBe("2mo ago");
  });

  it("returns 'just now' for future times", () => {
    const d = new Date(Date.now() + 60_000).toISOString();
    expect(timeAgo(d)).toBe("just now");
  });
});

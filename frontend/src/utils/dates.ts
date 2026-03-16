/**
 * Date parsing helpers — never use `new Date(str)` directly.
 * parseLocalDate() for date-only strings, parseDate() for datetime strings.
 */

/** Parse a date-only string like "2024-03-15" as local midnight. */
export function parseLocalDate(dateStr: string): Date {
  const [year, month, day] = dateStr.split("-").map(Number);
  if (year === undefined || month === undefined || day === undefined) {
    throw new Error(`Invalid date string: ${dateStr}`);
  }
  return new Date(year, month - 1, day);
}

/** Parse an ISO-8601 datetime string. */
export function parseDate(isoStr: string): Date {
  const d = new Date(isoStr);
  if (isNaN(d.getTime())) {
    throw new Error(`Invalid datetime string: ${isoStr}`);
  }
  return d;
}

/** Return a relative time string like "2 min ago". */
export function timeAgo(isoStr: string): string {
  const now = Date.now();
  const then = parseDate(isoStr).getTime();
  const diffMs = now - then;

  if (diffMs < 0) return "just now";

  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return seconds <= 5 ? "just now" : `${seconds}s ago`;

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;

  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}

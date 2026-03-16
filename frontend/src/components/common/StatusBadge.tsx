import "./StatusBadge.css";

interface StatusBadgeProps {
  status: string;
  size?: "sm" | "md";
}

const statusColors: Record<string, string> = {
  active: "var(--color-status-green)",
  idle: "var(--color-text-muted)",
  working: "var(--color-accent)",
  connecting: "var(--color-status-amber)",
  waiting: "var(--color-status-amber)",
  planning: "var(--color-accent)",
  managing: "var(--color-accent)",
  paused: "var(--color-text-muted)",
  disconnected: "var(--color-status-red)",
  error: "var(--color-status-red)",
  pending: "var(--color-text-muted)",
  assigned: "var(--color-accent)",
  in_progress: "var(--color-accent)",
  blocked: "var(--color-status-red)",
  done: "var(--color-status-green)",
  cancelled: "var(--color-text-muted)",
  ok: "var(--color-status-green)",
  warn: "var(--color-status-amber)",
  exceeded: "var(--color-status-red)",
  archived: "var(--color-text-muted)",
};

export default function StatusBadge({ status, size = "md" }: StatusBadgeProps) {
  const color = statusColors[status] ?? "var(--color-text-muted)";
  const label = status.replace(/_/g, " ");

  return (
    <span
      className={`status-badge status-badge--${size}`}
      data-testid="status-badge"
    >
      <span className="status-badge__dot" style={{ background: color }} />
      {label}
    </span>
  );
}

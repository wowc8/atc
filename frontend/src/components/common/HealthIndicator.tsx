import "./HealthIndicator.css";

interface HealthIndicatorProps {
  health: "alive" | "stale" | "stopped" | undefined;
  size?: "sm" | "md";
}

const healthColors: Record<string, string> = {
  alive: "var(--color-status-green)",
  stale: "var(--color-status-amber)",
  stopped: "var(--color-status-red)",
};

const healthLabels: Record<string, string> = {
  alive: "healthy",
  stale: "stale",
  stopped: "stopped",
};

export default function HealthIndicator({
  health,
  size = "sm",
}: HealthIndicatorProps) {
  if (!health) return null;

  const color = healthColors[health] ?? "var(--color-text-muted)";
  const label = healthLabels[health] ?? health;

  return (
    <span
      className={`health-indicator health-indicator--${size}`}
      data-testid="health-indicator"
      title={`Heartbeat: ${label}`}
    >
      <span
        className={`health-indicator__dot ${health === "alive" ? "health-indicator__dot--pulse" : ""}`}
        style={{ background: color }}
      />
    </span>
  );
}

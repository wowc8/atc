import { useState } from "react";
import type { TowerStatus } from "../../types";
import "./TowerBanner.css";

interface TowerBannerProps {
  towerStatus: TowerStatus;
}

function StatusDot({ status }: { status: string }) {
  const colorMap: Record<string, string> = {
    idle: "var(--color-text-muted)",
    planning: "var(--color-accent)",
    warning: "var(--color-status-amber)",
    error: "var(--color-status-red)",
  };
  return (
    <span
      className="tower-banner__dot"
      style={{ background: colorMap[status] ?? "var(--color-text-muted)" }}
    />
  );
}

export default function TowerBanner({ towerStatus }: TowerBannerProps) {
  const [minimized, setMinimized] = useState(true);

  return (
    <div
      className={`tower-banner ${minimized ? "tower-banner--minimized" : ""}`}
      data-testid="tower-banner"
    >
      <div className="tower-banner__bar">
        <button
          className="tower-banner__toggle"
          onClick={() => setMinimized((m) => !m)}
          aria-label={minimized ? "Expand Tower" : "Minimize Tower"}
        >
          <span className="tower-banner__caret">
            {minimized ? "\u25B8" : "\u25BE"}
          </span>
          <span className="tower-banner__label">Tower</span>
        </button>

        <StatusDot status={towerStatus.status} />
        <span className="tower-banner__status">{towerStatus.status}</span>

        <span className="tower-banner__ticker">
          {towerStatus.message || "Idle"}
        </span>
      </div>

      {!minimized && (
        <div className="tower-banner__detail" data-testid="tower-banner-detail">
          <p className="tower-banner__message">
            {towerStatus.message || "No current activity."}
          </p>
          <span className="tower-banner__meta">
            {towerStatus.active_projects} active project
            {towerStatus.active_projects !== 1 ? "s" : ""}
          </span>
        </div>
      )}
    </div>
  );
}

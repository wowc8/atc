import { useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useAppContext } from "../../context/AppContext";
import LogViewer from "./LogViewer";
import "./TowerBar.css";

function StatusDot({ status }: { status: string }) {
  const colorMap: Record<string, string> = {
    idle: "var(--color-text-muted)",
    planning: "var(--color-accent)",
    warning: "var(--color-status-amber)",
    error: "var(--color-status-red)",
  };
  return (
    <span
      className="tower-status-dot"
      style={{ background: colorMap[status] ?? "var(--color-text-muted)" }}
      title={`Tower: ${status}`}
    />
  );
}

export default function TowerBar() {
  const navigate = useNavigate();
  const location = useLocation();
  const { state } = useAppContext();
  const { brainStatus, usage, notifications, projects, failureLogs } = state;
  const [logViewerOpen, setLogViewerOpen] = useState(false);

  const activeProjects = projects.filter((p) => p.status === "active").length;
  const unreadCount = notifications.filter((n) => !n.read).length;
  const unresolvedLogCount = failureLogs.filter((f) => !f.resolved).length;

  const isActive = (path: string) => location.pathname.startsWith(path);

  return (
    <>
      <header className="tower-bar" data-testid="tower-bar">
        <div className="tower-bar__left">
          <button
            className="tower-bar__brand"
            onClick={() => navigate("/dashboard")}
          >
            ATC
          </button>

          <div className="tower-bar__status">
            <StatusDot status={brainStatus.status} />
            <span className="tower-bar__status-text">{brainStatus.status}</span>
          </div>
        </div>

        <nav className="tower-bar__nav">
          <button
            className={`tower-bar__nav-item ${isActive("/dashboard") ? "active" : ""}`}
            onClick={() => navigate("/dashboard")}
          >
            Dashboard
          </button>
          <button
            className={`tower-bar__nav-item ${isActive("/usage") ? "active" : ""}`}
            onClick={() => navigate("/usage")}
          >
            Usage
          </button>
          <button
            className={`tower-bar__nav-item ${isActive("/settings") ? "active" : ""}`}
            onClick={() => navigate("/settings")}
          >
            Settings
          </button>
        </nav>

        <div className="tower-bar__right">
          {brainStatus.message && (
            <span className="tower-bar__message" title={brainStatus.message}>
              {brainStatus.message}
            </span>
          )}

          <span className="tower-bar__metric" data-testid="cost-summary">
            ${usage.today_cost.toFixed(2)} today
          </span>

          <span className="tower-bar__metric" data-testid="token-summary">
            {formatTokens(usage.today_tokens)} tokens
          </span>

          <span className="tower-bar__metric" data-testid="project-count">
            {activeProjects} project{activeProjects !== 1 ? "s" : ""}
          </span>

          <button
            className="tower-bar__icon-btn"
            data-testid="failure-log-btn"
            onClick={() => setLogViewerOpen((prev) => !prev)}
            title={`${unresolvedLogCount} unresolved failure${unresolvedLogCount !== 1 ? "s" : ""}`}
          >
            <FailureLogIcon />
            {unresolvedLogCount > 0 && (
              <span className="tower-bar__badge" data-testid="failure-log-badge">
                {unresolvedLogCount}
              </span>
            )}
          </button>

          <button
            className="tower-bar__icon-btn"
            data-testid="notification-bell"
            onClick={() => navigate("/dashboard")}
            title={`${unreadCount} unread notification${unreadCount !== 1 ? "s" : ""}`}
          >
            <NotificationIcon />
            {unreadCount > 0 && (
              <span className="tower-bar__badge">{unreadCount}</span>
            )}
          </button>

          <button
            className="tower-bar__icon-btn"
            data-testid="settings-button"
            onClick={() => navigate("/settings")}
            title="Settings"
          >
            <SettingsIcon />
          </button>
        </div>
      </header>

      {logViewerOpen && <LogViewer onClose={() => setLogViewerOpen(false)} />}
    </>
  );
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}k`;
  return String(n);
}

function FailureLogIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
      <path d="M8.982 1.566a1.13 1.13 0 0 0-1.96 0L.165 13.233c-.457.778.091 1.767.98 1.767h13.713c.889 0 1.438-.99.98-1.767L8.982 1.566zM8 5c.535 0 .954.462.9.995l-.35 3.507a.552.552 0 0 1-1.1 0L7.1 5.995A.905.905 0 0 1 8 5zm.002 6a1 1 0 1 1 0 2 1 1 0 0 1 0-2z" />
    </svg>
  );
}

function NotificationIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
      <path d="M8 16a2 2 0 0 0 2-2H6a2 2 0 0 0 2 2zm6-5V7a6 6 0 0 0-5-5.91V0H7v1.09A6 6 0 0 0 2 7v4l-1.5 1.5V14h15v-1.5L14 11z" />
    </svg>
  );
}

function SettingsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
      <path d="M8 4.754a3.246 3.246 0 1 0 0 6.492 3.246 3.246 0 0 0 0-6.492zM5.754 8a2.246 2.246 0 1 1 4.492 0 2.246 2.246 0 0 1-4.492 0z" />
      <path d="M9.796 1.343c-.527-1.79-3.065-1.79-3.592 0l-.094.319a.873.873 0 0 1-1.255.52l-.292-.16c-1.64-.892-3.433.902-2.54 2.541l.159.292a.873.873 0 0 1-.52 1.255l-.319.094c-1.79.527-1.79 3.065 0 3.592l.319.094a.873.873 0 0 1 .52 1.255l-.16.292c-.892 1.64.901 3.434 2.541 2.54l.292-.159a.873.873 0 0 1 1.255.52l.094.319c.527 1.79 3.065 1.79 3.592 0l.094-.319a.873.873 0 0 1 1.255-.52l.292.16c1.64.893 3.434-.902 2.54-2.541l-.159-.292a.873.873 0 0 1 .52-1.255l.319-.094c1.79-.527 1.79-3.065 0-3.592l-.319-.094a.873.873 0 0 1-.52-1.255l.16-.292c.893-1.64-.902-3.433-2.541-2.54l-.292.159a.873.873 0 0 1-1.255-.52l-.094-.319zm-2.633.283c.246-.835 1.428-.835 1.674 0l.094.319a1.873 1.873 0 0 0 2.693 1.115l.291-.16c.764-.415 1.6.42 1.184 1.185l-.159.292a1.873 1.873 0 0 0 1.116 2.692l.318.094c.835.246.835 1.428 0 1.674l-.319.094a1.873 1.873 0 0 0-1.115 2.693l.16.291c.415.764-.421 1.6-1.185 1.184l-.291-.159a1.873 1.873 0 0 0-2.693 1.116l-.094.318c-.246.835-1.428.835-1.674 0l-.094-.319a1.873 1.873 0 0 0-2.692-1.115l-.292.16c-.764.415-1.6-.421-1.184-1.185l.159-.291A1.873 1.873 0 0 0 1.945 8.93l-.319-.094c-.835-.246-.835-1.428 0-1.674l.319-.094A1.873 1.873 0 0 0 3.06 4.377l-.16-.292c-.415-.764.42-1.6 1.185-1.184l.292.159a1.873 1.873 0 0 0 2.692-1.116l.094-.318z" />
    </svg>
  );
}

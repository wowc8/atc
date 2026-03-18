import { useState, useEffect, useCallback, useRef } from "react";
import { useLocation } from "react-router-dom";
import { useAppContext } from "../../context/AppContext";
import { useTerminal } from "../../hooks/useTerminal";
import { api } from "../../utils/api";
import "./TowerPanel.css";

/**
 * Persistent bottom panel for Tower — lives in the shell layout,
 * persists across all pages. Think VS Code integrated terminal.
 *
 * Context-aware: automatically infers the active project from the
 * current route (e.g. /projects/:id). No project dropdown needed.
 *
 * Minimized: single-line ticker showing latest Tower activity.
 * Expanded + idle: terminal-style input at bottom.
 * Expanded + running: full PTY terminal + message input bar.
 */
export default function TowerPanel() {
  const { state } = useAppContext();
  const { towerDetail, towerProgress, brainStatus, projects } = state;

  const location = useLocation();
  const routeProjectId = useRouteProjectId();

  const [expanded, setExpanded] = useState(false);
  const [input, setInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const messageInputRef = useRef<HTMLInputElement>(null);

  const isRunning =
    towerDetail.state === "planning" || towerDetail.state === "managing";
  const isIdle =
    towerDetail.state === "idle" ||
    towerDetail.state === "complete" ||
    towerDetail.state === "error";

  const terminalChannel = towerDetail.current_session_id
    ? `terminal:${towerDetail.current_session_id}`
    : undefined;

  const { attachRef, fit } = useTerminal({
    channel: terminalChannel,
    enabled: isRunning && !!terminalChannel,
  });

  // Re-fit terminal when panel expands
  useEffect(() => {
    if (expanded && isRunning) {
      const timer = setTimeout(() => fit(), 200);
      return () => clearTimeout(timer);
    }
  }, [expanded, isRunning, fit]);

  // Auto-expand when Tower starts running
  useEffect(() => {
    if (isRunning) {
      setExpanded(true);
    }
  }, [isRunning]);

  // Focus input when expanded and idle
  useEffect(() => {
    if (expanded && isIdle) {
      inputRef.current?.focus();
    }
  }, [expanded, isIdle]);

  // Derive the active project from the route
  const contextProject = routeProjectId
    ? projects.find((p) => p.id === routeProjectId)
    : null;

  // Determine which project ID to use: route context, or first active project
  const resolvedProjectId =
    routeProjectId ??
    (projects.find((p) => p.status === "active")?.id || "");

  const contextLabel = deriveContextLabel(location.pathname, contextProject?.name);

  const handleSubmitGoal = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!input.trim() || !resolvedProjectId) return;
      setSubmitting(true);
      try {
        await api.post("/tower/goal", {
          project_id: resolvedProjectId,
          goal: input.trim(),
        });
        setInput("");
      } catch (err) {
        console.error("Failed to set tower goal:", err);
      } finally {
        setSubmitting(false);
      }
    },
    [input, resolvedProjectId],
  );

  const handleCancel = useCallback(async () => {
    try {
      await api.post("/tower/cancel");
    } catch (err) {
      console.error("Failed to cancel tower goal:", err);
    }
  }, []);

  const handleSendMessage = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!message.trim()) return;
      setSending(true);
      try {
        await api.post("/tower/message", { message: message.trim() });
        setMessage("");
        messageInputRef.current?.focus();
      } catch (err) {
        console.error("Failed to send message to Leader:", err);
      } finally {
        setSending(false);
      }
    },
    [message],
  );

  const handleMarkComplete = useCallback(async () => {
    try {
      await api.post("/tower/complete");
    } catch (err) {
      console.error("Failed to mark Tower goal complete:", err);
    }
  }, []);

  const tickerText =
    brainStatus.message ||
    (towerDetail.current_goal
      ? `Goal: ${towerDetail.current_goal}`
      : "Idle");

  return (
    <div
      className={`tower-panel ${expanded ? "tower-panel--expanded" : ""}`}
      data-testid="tower-panel"
    >
      {/* Minimized bar -- always visible */}
      <div className="tower-panel__bar">
        <button
          className="tower-panel__toggle"
          onClick={() => setExpanded((v) => !v)}
          aria-label={expanded ? "Minimize Tower" : "Expand Tower"}
          data-testid="tower-panel-toggle"
        >
          <span className="tower-panel__caret">
            {expanded ? "\u25BE" : "\u25B4"}
          </span>
          <span className="tower-panel__label">Tower</span>
        </button>

        <TowerStateDot state={towerDetail.state} />

        <span
          className="tower-panel__context"
          title={contextLabel}
          data-testid="tower-panel-context"
        >
          {contextLabel}
        </span>

        <span className="tower-panel__ticker" title={tickerText}>
          {tickerText}
        </span>

        <div className="tower-panel__bar-actions">
          {isRunning && towerProgress.total > 0 && (
            <span
              className="tower-panel__progress"
              data-testid="tower-panel-progress"
            >
              {towerProgress.done}/{towerProgress.total} tasks ({towerProgress.progress_pct}%)
            </span>
          )}
          {isRunning && (
            <>
              <button
                className="btn btn-sm"
                onClick={handleMarkComplete}
                data-testid="tower-panel-complete"
              >
                Complete
              </button>
              <button
                className="btn btn-danger btn-sm"
                onClick={handleCancel}
                data-testid="tower-panel-cancel"
              >
                Cancel
              </button>
            </>
          )}
        </div>
      </div>

      {/* Expanded content */}
      {expanded && (
        <div className="tower-panel__content" data-testid="tower-panel-content">
          {/* Terminal area -- always present when running */}
          {isRunning && (
            <div
              className="tower-panel__terminal"
              ref={attachRef}
              data-testid="tower-panel-terminal"
            />
          )}

          {/* Error state */}
          {towerDetail.state === "error" && towerDetail.current_goal && (
            <div className="tower-panel__error">
              Tower encountered an error while processing:{" "}
              <strong>{towerDetail.current_goal}</strong>
            </div>
          )}

          {/* Input bar -- always at the bottom */}
          {isRunning ? (
            <form
              className="tower-panel__input-bar"
              onSubmit={handleSendMessage}
              data-testid="tower-panel-message-form"
            >
              <span className="tower-panel__prompt">&gt;</span>
              <input
                ref={messageInputRef}
                type="text"
                className="tower-panel__input"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="Send message to Tower..."
                disabled={sending}
                data-testid="tower-panel-message"
              />
              <button
                type="submit"
                className="btn btn-primary btn-sm"
                disabled={sending || !message.trim()}
                data-testid="tower-panel-send"
              >
                {sending ? "..." : "Send"}
              </button>
            </form>
          ) : (
            <form
              className="tower-panel__input-bar"
              onSubmit={handleSubmitGoal}
              data-testid="tower-panel-goal-form"
            >
              <span className="tower-panel__prompt">&gt;</span>
              <input
                ref={inputRef}
                type="text"
                className="tower-panel__input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder={
                  resolvedProjectId
                    ? "Describe a goal for Tower..."
                    : "No active projects"
                }
                disabled={submitting || !resolvedProjectId}
                data-testid="tower-panel-goal"
              />
              <button
                type="submit"
                className="btn btn-primary btn-sm"
                disabled={submitting || !input.trim() || !resolvedProjectId}
                data-testid="tower-panel-start"
              >
                {submitting ? "..." : "Start"}
              </button>
            </form>
          )}
        </div>
      )}
    </div>
  );
}

function TowerStateDot({ state }: { state: string }) {
  const colorMap: Record<string, string> = {
    idle: "var(--color-text-muted)",
    planning: "var(--color-accent)",
    managing: "var(--color-status-green)",
    complete: "var(--color-status-green)",
    error: "var(--color-status-red)",
  };
  return (
    <span
      className="tower-panel__dot"
      style={{ background: colorMap[state] ?? "var(--color-text-muted)" }}
      title={`Tower: ${state}`}
    />
  );
}

/** Extract project ID from /projects/:id route */
function useRouteProjectId(): string | undefined {
  const location = useLocation();
  const match = location.pathname.match(/^\/projects\/([^/]+)/);
  return match?.[1];
}

/** Derive a human-readable context label from the current route */
function deriveContextLabel(
  pathname: string,
  projectName: string | undefined,
): string {
  if (pathname.startsWith("/projects/") && projectName) {
    return projectName;
  }
  if (pathname === "/dashboard" || pathname === "/") {
    return "Dashboard";
  }
  if (pathname === "/settings") {
    return "Settings";
  }
  if (pathname === "/usage") {
    return "Usage";
  }
  return "ATC";
}

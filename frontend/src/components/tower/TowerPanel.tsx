import { useState, useEffect, useCallback, useRef } from "react";
import { useLocation } from "react-router-dom";
import { useAppContext } from "../../context/AppContext";
import { useTerminal } from "../../hooks/useTerminal";
import { api } from "../../utils/api";
import ConfirmPopover from "../common/ConfirmPopover";
import "./TowerPanel.css";

/**
 * Persistent bottom panel for Tower — lives in the shell layout,
 * persists across all pages. Think VS Code integrated terminal.
 *
 * For claude_code provider: auto-starts Tower's own Claude Code session
 * on mount (no goal form). Tower has its own independent terminal session,
 * separate from the Leader session.
 *
 * Minimized: single-line ticker showing latest Tower activity.
 * Expanded + running: full PTY terminal + message input bar.
 */
export default function TowerPanel() {
  const { state, dispatch } = useAppContext();
  const { towerDetail, towerProgress, brainStatus, projects } = state;

  const location = useLocation();
  const routeProjectId = useRouteProjectId();

  const [expanded, setExpanded] = useState(false);
  const [message, setMessage] = useState("");
  const [starting, setStarting] = useState(false);
  const messageInputRef = useRef<HTMLInputElement>(null);
  const autoStarted = useRef(false);
  const userStopped = useRef(false);

  const isRunning =
    towerDetail.state === "planning" || towerDetail.state === "managing";
  const isIdle =
    towerDetail.state === "idle" ||
    towerDetail.state === "complete" ||
    towerDetail.state === "error";

  // Derive the active project from the route
  const contextProject = routeProjectId
    ? projects.find((p) => p.id === routeProjectId)
    : null;

  // Determine which project ID to use: route context, or first active project
  const resolvedProjectId =
    routeProjectId ??
    (projects.find((p) => p.status === "active")?.id || "");

  const resolvedProject = resolvedProjectId
    ? projects.find((p) => p.id === resolvedProjectId)
    : null;

  const isClaudeCode = resolvedProject?.agent_provider === "claude_code";

  // Tower's own terminal channel (NOT the Leader's)
  const terminalChannel = towerDetail.current_session_id
    ? `terminal:${towerDetail.current_session_id}`
    : undefined;

  const { attachRef, fit } = useTerminal({
    channel: terminalChannel,
    enabled: (isRunning || (isClaudeCode && !!terminalChannel)) && !!terminalChannel,
  });

  // Reset auto-start flag when the resolved project changes so Tower
  // can auto-start for a newly created or newly navigated-to project.
  useEffect(() => {
    autoStarted.current = false;
    userStopped.current = false;
  }, [resolvedProjectId]);

  // Auto-start Tower session for claude_code provider.
  // Respects userStopped ref to prevent re-starting after manual Stop.
  useEffect(() => {
    if (
      isClaudeCode &&
      isIdle &&
      !starting &&
      !autoStarted.current &&
      !userStopped.current &&
      resolvedProjectId
    ) {
      autoStarted.current = true;
      void handleStart();
    }
  }, [isClaudeCode, isIdle, starting, resolvedProjectId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Re-fit terminal when panel expands
  useEffect(() => {
    if (expanded && (isRunning || (isClaudeCode && !!terminalChannel))) {
      const timer = setTimeout(() => fit(), 200);
      return () => clearTimeout(timer);
    }
  }, [expanded, isRunning, isClaudeCode, terminalChannel, fit]);

  // Auto-expand when Tower starts running
  useEffect(() => {
    if (isRunning) {
      setExpanded(true);
    }
  }, [isRunning]);

  const contextLabel = deriveContextLabel(location.pathname, contextProject?.name);

  const handleStart = useCallback(async () => {
    if (!resolvedProjectId) return;
    userStopped.current = false;
    setStarting(true);
    try {
      const res = await api.post<{ session_id?: string }>(
        "/tower/start",
        { project_id: resolvedProjectId },
      );
      if (res.session_id) {
        dispatch({
          type: "SET_TOWER_DETAIL",
          payload: {
            current_session_id: res.session_id,
            current_project_id: resolvedProjectId,
          },
        });
      }
      setExpanded(true);
    } catch (err) {
      console.error("Failed to start tower session:", err);
    } finally {
      setStarting(false);
    }
  }, [resolvedProjectId, dispatch]);

  const handleStop = useCallback(async () => {
    // Set userStopped BEFORE any state changes to prevent the auto-start
    // useEffect from firing during the async gap or re-render.
    userStopped.current = true;
    try {
      await api.post("/tower/stop");
      dispatch({
        type: "SET_TOWER_DETAIL",
        payload: {
          state: "idle",
          current_session_id: null,
          leader_session_id: null,
          current_goal: null,
        },
      });
    } catch (err) {
      console.error("Failed to stop tower:", err);
      userStopped.current = false;
    }
  }, [dispatch]);

  const [sendError, setSendError] = useState<string | null>(null);

  const handleSendMessage = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!message.trim()) return;
      const text = message.trim();
      setMessage("");
      setSendError(null);
      messageInputRef.current?.focus();
      try {
        await api.post("/tower/message", { message: text });
      } catch (err) {
        console.error("Failed to send message to Tower:", err);
        setSendError("Failed to send message");
        // Restore message so user can retry
        setMessage(text);
      }
    },
    [message],
  );

  const tickerText =
    brainStatus.message ||
    (towerDetail.current_goal
      ? `Goal: ${towerDetail.current_goal}`
      : "Idle");

  const showTerminal = isRunning || (isClaudeCode && !!terminalChannel);

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
          {!showTerminal && (
            <button
              className="btn btn-primary btn-sm"
              onClick={handleStart}
              disabled={starting || !resolvedProjectId}
              data-testid="tower-panel-start"
            >
              {starting ? "Starting..." : "Start"}
            </button>
          )}
          {showTerminal && (
            <ConfirmPopover
              message="Stop the Tower session?"
              confirmLabel="Stop"
              onConfirm={handleStop}
              variant="danger"
            >
              <button
                className="btn btn-danger btn-sm"
                data-testid="tower-panel-stop"
              >
                Stop
              </button>
            </ConfirmPopover>
          )}
        </div>
      </div>

      {/* Expanded content — kept in DOM to preserve xterm instance */}
      <div
        className="tower-panel__content"
        data-testid="tower-panel-content"
        style={{ display: expanded ? undefined : "none" }}
      >
        {/* Terminal area */}
        {showTerminal && (
          <div
            className="tower-panel__terminal"
            ref={attachRef}
            data-testid="tower-panel-terminal"
          />
        )}

        {/* Loading state for auto-start */}
        {isClaudeCode && !showTerminal && starting && (
          <div className="tower-panel__loading">Starting Tower terminal...</div>
        )}

        {/* Error state */}
        {towerDetail.state === "error" && towerDetail.current_goal && (
          <div className="tower-panel__error">
            Tower encountered an error while processing:{" "}
            <strong>{towerDetail.current_goal}</strong>
          </div>
        )}

        {/* Message input bar — always at the bottom when terminal is showing */}
        {showTerminal && (
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
              data-testid="tower-panel-message"
            />
            <button
              type="submit"
              className="btn btn-primary btn-sm"
              disabled={!message.trim()}
              data-testid="tower-panel-send"
            >
              Send
            </button>
            {sendError && (
              <span className="tower-panel__send-error" title={sendError}>
                !
              </span>
            )}
          </form>
        )}
      </div>
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
  if (pathname === "/usage") {
    return "Usage";
  }
  return "ATC";
}

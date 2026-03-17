import { useState, useEffect, useCallback } from "react";
import { useAppContext } from "../../context/AppContext";
import { useTerminal } from "../../hooks/useTerminal";
import { api } from "../../utils/api";
import "./TowerPanel.css";

/**
 * Persistent bottom panel for Tower — lives in the shell layout,
 * persists across all pages. Think VS Code integrated terminal.
 *
 * Minimized: single-line ticker showing latest Tower activity.
 * Expanded + idle: goal input form.
 * Expanded + running: full PTY terminal streaming Leader output.
 */
export default function TowerPanel() {
  const { state } = useAppContext();
  const { towerDetail, brainStatus, projects } = state;

  const [expanded, setExpanded] = useState(false);
  const [goal, setGoal] = useState("");
  const [projectId, setProjectId] = useState("");
  const [submitting, setSubmitting] = useState(false);

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
      // Delay to let CSS transition finish
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

  // Default to first active project
  useEffect(() => {
    if (!projectId && projects.length > 0) {
      const active = projects.find((p) => p.status === "active");
      if (active) setProjectId(active.id);
    }
  }, [projectId, projects]);

  const handleSubmitGoal = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!goal.trim() || !projectId) return;
      setSubmitting(true);
      try {
        await api.post("/tower/goal", {
          project_id: projectId,
          goal: goal.trim(),
        });
        setGoal("");
      } catch (err) {
        console.error("Failed to set tower goal:", err);
      } finally {
        setSubmitting(false);
      }
    },
    [goal, projectId],
  );

  const handleCancel = useCallback(async () => {
    try {
      await api.post("/tower/cancel");
    } catch (err) {
      console.error("Failed to cancel tower goal:", err);
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
      {/* Minimized bar — always visible */}
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

        <span className="tower-panel__ticker" title={tickerText}>
          {tickerText}
        </span>

        <div className="tower-panel__bar-actions">
          {isRunning && (
            <button
              className="btn btn-danger btn-sm"
              onClick={handleCancel}
              data-testid="tower-panel-cancel"
            >
              Cancel
            </button>
          )}
        </div>
      </div>

      {/* Expanded content */}
      {expanded && (
        <div className="tower-panel__content" data-testid="tower-panel-content">
          {isIdle && (
            <form
              className="tower-panel__goal-form"
              onSubmit={handleSubmitGoal}
            >
              <select
                className="tower-panel__project-select"
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                data-testid="tower-panel-project"
              >
                <option value="" disabled>
                  Select project...
                </option>
                {projects
                  .filter((p) => p.status === "active")
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
              </select>
              <input
                type="text"
                className="tower-panel__goal-input"
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                placeholder="Describe a goal for Tower..."
                data-testid="tower-panel-goal"
              />
              <button
                type="submit"
                className="btn btn-primary btn-sm"
                disabled={submitting || !goal.trim() || !projectId}
                data-testid="tower-panel-start"
              >
                {submitting ? "Starting..." : "Start"}
              </button>
            </form>
          )}

          {isRunning && (
            <div
              className="tower-panel__terminal"
              ref={attachRef}
              data-testid="tower-panel-terminal"
            />
          )}

          {towerDetail.state === "error" && towerDetail.current_goal && (
            <div className="tower-panel__error">
              Tower encountered an error while processing:{" "}
              <strong>{towerDetail.current_goal}</strong>
            </div>
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

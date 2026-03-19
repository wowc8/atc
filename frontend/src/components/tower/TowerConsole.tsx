import { useState, useRef, useEffect } from "react";
import { useAppContext } from "../../context/AppContext";
import { useTerminal } from "../../hooks/useTerminal";
import { api } from "../../utils/api";
import StatusBadge from "../common/StatusBadge";
import "./TowerConsole.css";

/**
 * Full interactive terminal panel for the Tower Claude session.
 *
 * For claude_code provider: auto-starts as an open terminal on app load.
 * For other providers: shows goal form with Start button.
 * Running: Full PTY terminal + message input bar.
 */
export default function TowerConsole() {
  const { state, dispatch } = useAppContext();
  const { towerDetail, towerProgress, brainStatus, projects } = state;

  const [goal, setGoal] = useState("");
  const [projectId, setProjectId] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const messageInputRef = useRef<HTMLInputElement>(null);
  const autoStarted = useRef(false);

  const isRunning =
    towerDetail.state === "planning" || towerDetail.state === "managing";
  const isIdle =
    towerDetail.state === "idle" ||
    towerDetail.state === "complete" ||
    towerDetail.state === "error";

  // Check if the default project uses claude_code provider
  const activeProject = projects.find((p) => p.status === "active");
  const isClaudeCode = activeProject?.agent_provider === "claude_code";

  const terminalChannel = towerDetail.current_session_id
    ? `terminal:${towerDetail.current_session_id}`
    : undefined;

  const { attachRef, sendInput } = useTerminal({
    channel: terminalChannel,
    enabled: (isRunning || (isClaudeCode && !!terminalChannel)) && !!terminalChannel,
  });

  // Default to first active project
  if (!projectId && projects.length > 0) {
    if (activeProject) setProjectId(activeProject.id);
  }

  // Auto-start for claude_code provider on app load
  useEffect(() => {
    if (isClaudeCode && isIdle && !loading && !autoStarted.current && projectId) {
      autoStarted.current = true;
      void handleStart();
    }
  }, [isClaudeCode, isIdle, loading, projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleStart() {
    if (!projectId) return;
    setLoading(true);
    try {
      if (isClaudeCode) {
        // For claude_code: start Tower's own Claude Code session
        const res = await api.post<{ session_id?: string }>("/tower/start", {
          project_id: projectId,
        });
        if (res.session_id) {
          dispatch({
            type: "SET_TOWER_DETAIL",
            payload: { current_session_id: res.session_id },
          });
        }
      } else {
        // For other providers: submit a goal to start the Leader
        const res = await api.post<{ session_id?: string }>("/tower/goal", {
          project_id: projectId,
          goal: goal.trim() || null,
        });
        setGoal("");
        if (res.session_id) {
          dispatch({
            type: "SET_TOWER_DETAIL",
            payload: { current_session_id: res.session_id },
          });
        }
      }
    } catch (err) {
      console.error("Failed to start Tower:", err);
    } finally {
      setLoading(false);
    }
  }

  async function handleStop() {
    setLoading(true);
    try {
      await api.post("/tower/stop");
      autoStarted.current = true;
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
      console.error("Failed to stop Tower:", err);
    } finally {
      setLoading(false);
    }
  }

  async function handleComplete() {
    try {
      await api.post("/tower/complete");
    } catch (err) {
      console.error("Failed to mark Tower goal complete:", err);
    }
  }

  function handleSendMessage(e: React.FormEvent) {
    e.preventDefault();
    if (!message.trim()) return;
    sendInput(message.trim());
    setMessage("");
    messageInputRef.current?.focus();
  }

  const statusLabel = brainStatus.status ?? towerDetail.state;

  return (
    <div className="tower-console" data-testid="tower-console">
      {/* Header */}
      <div className="tower-console__header">
        <h3>Tower</h3>
        <div className="tower-console__controls">
          <StatusBadge status={statusLabel} size="sm" />
          {isRunning && towerProgress.total > 0 && (
            <span
              className="tower-console__progress"
              data-testid="tower-console-progress"
            >
              {towerProgress.done}/{towerProgress.total}
            </span>
          )}
          {!isRunning ? (
            <button
              className="btn btn-primary btn-sm"
              onClick={handleStart}
              disabled={loading || !projectId}
              data-testid="tower-console-start"
            >
              {loading ? "Starting..." : "Start"}
            </button>
          ) : (
            <>
              <button
                className="btn btn-sm"
                onClick={handleComplete}
                data-testid="tower-console-complete"
              >
                Complete
              </button>
              <button
                className="btn btn-danger btn-sm"
                onClick={handleStop}
                disabled={loading}
                data-testid="tower-console-stop"
              >
                Stop
              </button>
            </>
          )}
        </div>
      </div>

      {/* Idle: goal form (only for non-claude_code providers) */}
      {isIdle && !isClaudeCode && (
        <div className="tower-console__start-form">
          <div className="form-group">
            <label htmlFor="tower-project">Project</label>
            <select
              id="tower-project"
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
              data-testid="tower-console-project"
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
          </div>
          <div className="form-group">
            <label htmlFor="tower-goal">Goal (optional)</label>
            <input
              id="tower-goal"
              type="text"
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="Describe a goal for Tower..."
              onKeyDown={(e) => {
                if (e.key === "Enter" && projectId) handleStart();
              }}
              data-testid="tower-console-goal"
            />
          </div>
        </div>
      )}

      {/* Claude Code auto-starting indicator */}
      {isIdle && isClaudeCode && loading && (
        <div className="tower-console__loading">Starting terminal...</div>
      )}

      {/* Current goal display */}
      {towerDetail.current_goal && (
        <p
          className="tower-console__goal"
          data-testid="tower-console-current-goal"
        >
          {towerDetail.current_goal}
        </p>
      )}

      {/* Error state */}
      {towerDetail.state === "error" && (
        <div className="tower-console__error">
          Tower encountered an error.
          {towerDetail.current_goal && (
            <>
              {" "}
              Goal: <strong>{towerDetail.current_goal}</strong>
            </>
          )}
        </div>
      )}

      {/* Running: terminal + message input */}
      {(isRunning || (isClaudeCode && !!terminalChannel)) && (
        <>
          <div
            className="tower-console__terminal"
            ref={attachRef}
            data-testid="tower-console-terminal"
          />

          <form className="tower-console__input" onSubmit={handleSendMessage}>
            <input
              ref={messageInputRef}
              type="text"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="Send message to Tower..."
              data-testid="tower-console-message"
            />
            <button
              type="submit"
              className="btn btn-sm btn-primary"
              disabled={!message.trim()}
              data-testid="tower-console-send"
            >
              Send
            </button>
          </form>
        </>
      )}
    </div>
  );
}

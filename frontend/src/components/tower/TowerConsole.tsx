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
 * Running: Full PTY terminal (type directly into the terminal).
 */
export default function TowerConsole() {
  const { state, dispatch } = useAppContext();
  const { towerDetail, towerProgress, brainStatus, projects } = state;

  const [goal, setGoal] = useState("");
  const [projectId, setProjectId] = useState("");
  const [loading, setLoading] = useState(false);
  const autoStarted = useRef(false);
  const userStopped = useRef(false);

  const activeProject = projects.find((p) => p.status === "active");
  const selectedProject = projects.find((p) => p.id === projectId) ?? activeProject;
  const activeTowerProject = projects.find((p) => p.id === towerDetail.current_project_id) ?? null;
  const terminalBackedProviders = new Set(["claude_code", "codex"]);

  const selectedProvider = selectedProject?.agent_provider ?? null;
  const activeTowerProvider = activeTowerProject?.agent_provider ?? null;
  const isTerminalProvider = selectedProvider !== null && terminalBackedProviders.has(selectedProvider);
  const towerSessionProvider = towerDetail.current_session_id ? activeTowerProvider : null;
  const towerProviderMismatch = Boolean(
    towerDetail.current_session_id &&
      selectedProject &&
      activeTowerProject &&
      selectedProvider &&
      towerSessionProvider &&
      selectedProject.id !== activeTowerProject.id &&
      selectedProvider !== towerSessionProvider,
  );

  const isRunning =
    towerDetail.state === "planning" || towerDetail.state === "managing";
  const isIdle =
    towerDetail.state === "idle" ||
    towerDetail.state === "complete" ||
    towerDetail.state === "error";

  const terminalChannel = towerDetail.current_session_id
    ? `terminal:${towerDetail.current_session_id}`
    : undefined;

  const { attachRef } = useTerminal({
    channel: terminalChannel,
    enabled: (isRunning || (isTerminalProvider && !!terminalChannel)) && !!terminalChannel,
  });

  if (!projectId && projects.length > 0) {
    if (activeProject) setProjectId(activeProject.id);
  }

  useEffect(() => {
    if (isTerminalProvider && isIdle && !loading && !autoStarted.current && !userStopped.current && projectId) {
      autoStarted.current = true;
      void handleStart();
    }
  }, [isTerminalProvider, isIdle, loading, projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleStart() {
    if (!projectId) return;
    userStopped.current = false;
    setLoading(true);
    try {
      if (isTerminalProvider) {
        const res = await api.post<{ session_id?: string }>("/tower/start", {
          project_id: projectId,
        });
        if (res.session_id) {
          dispatch({
            type: "SET_TOWER_DETAIL",
            payload: { current_session_id: res.session_id, current_project_id: projectId },
          });
        }
      } else {
        const res = await api.post<{ session_id?: string }>("/tower/goal", {
          project_id: projectId,
          goal: goal.trim() || null,
        });
        setGoal("");
        if (res.session_id) {
          dispatch({
            type: "SET_TOWER_DETAIL",
            payload: { current_session_id: res.session_id, current_project_id: projectId },
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
    userStopped.current = true;
    setLoading(true);
    try {
      await api.post("/tower/stop");
      dispatch({
        type: "SET_TOWER_DETAIL",
        payload: {
          state: "idle",
          current_session_id: null,
          current_project_id: null,
          leader_session_id: null,
          current_goal: null,
        },
      });
    } catch (err) {
      console.error("Failed to stop Tower:", err);
      userStopped.current = false;
    } finally {
      setLoading(false);
    }
  }

  async function handleRestartWithSelectedProvider() {
    if (!projectId || !selectedProvider) return;
    userStopped.current = false;
    setLoading(true);
    try {
      await api.patch(`/projects/${projectId}/agent-provider`, {
        agent_provider: selectedProvider,
      });
      if (towerDetail.current_session_id) {
        await api.post("/tower/stop");
      }
      const res = await api.post<{ session_id?: string }>("/tower/start", {
        project_id: projectId,
      });
      dispatch({
        type: "SET_TOWER_DETAIL",
        payload: {
          current_session_id: res.session_id ?? null,
          current_project_id: projectId,
          current_goal: null,
          leader_session_id: null,
        },
      });
    } catch (err) {
      console.error("Failed to restart Tower with selected provider:", err);
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

  const statusLabel = brainStatus.status ?? towerDetail.state;

  return (
    <div className="tower-console" data-testid="tower-console">
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

      {towerProviderMismatch && (
        <div className="tower-console__error" data-testid="tower-console-provider-mismatch">
          Tower is currently running with <strong>{towerSessionProvider}</strong>, but the selected project is set to <strong>{selectedProvider}</strong>.
          <button
            className="btn btn-sm"
            onClick={handleRestartWithSelectedProvider}
            disabled={loading}
            data-testid="tower-console-restart-provider"
          >
            {loading ? "Applying..." : "Apply to Tower and restart"}
          </button>
        </div>
      )}

      {isIdle && !isTerminalProvider && (
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

      {isIdle && isTerminalProvider && loading && (
        <div className="tower-console__loading">Starting terminal...</div>
      )}

      {towerDetail.current_goal && (
        <p
          className="tower-console__goal"
          data-testid="tower-console-current-goal"
        >
          {towerDetail.current_goal}
        </p>
      )}

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

      {(isRunning || (isTerminalProvider && !!terminalChannel)) && (
        <div
          className="tower-console__terminal"
          ref={attachRef}
          data-testid="tower-console-terminal"
        />
      )}
    </div>
  );
}

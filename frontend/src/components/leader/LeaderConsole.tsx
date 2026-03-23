import { useState, useEffect, useRef } from "react";
import { api } from "../../utils/api";
import { useTerminal } from "../../hooks/useTerminal";
import { useAppContext } from "../../context/AppContext";
import StatusBadge from "../common/StatusBadge";
import ConfirmPopover from "../common/ConfirmPopover";
import GitHubPanel from "../dashboard/GitHubPanel";
import BudgetPanel from "./BudgetPanel";
import type { Leader, Project } from "../../types";
import "./LeaderConsole.css";

interface LeaderConsoleProps {
  projectId: string;
  leader: Leader | undefined;
  project?: Project;
  onRefresh: () => Promise<void> | void;
}

type Tab = "tasks" | "github" | "budget";

const TABS: { id: Tab; label: string }[] = [
  { id: "tasks", label: "Tasks" },
  { id: "github", label: "GitHub" },
  { id: "budget", label: "Budget" },
];

export default function LeaderConsole({
  projectId,
  leader,
  project,
  onRefresh,
}: LeaderConsoleProps) {
  const { state, dispatch } = useAppContext();
  const [goal, setGoal] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("tasks");
  const autoStarted = useRef(false);
  const userStopped = useRef(false);

  const isClaudeCode = project?.agent_provider === "claude_code";

  const isRunning =
    leader?.status === "planning" || leader?.status === "managing";
  const isIdle =
    !leader || leader.status === "idle" || leader.status === undefined;

  const terminalChannel = leader?.session_id
    ? `terminal:${leader.session_id}`
    : undefined;

  const { attachRef } = useTerminal({
    channel: terminalChannel,
    enabled: (isRunning || (isClaudeCode && !!terminalChannel)) && !!terminalChannel,
  });

  // Reset auto-start flag when the project changes so Leader
  // can auto-start for a newly navigated-to project.
  useEffect(() => {
    autoStarted.current = false;
    userStopped.current = false;
  }, [projectId]);

  // Auto-start for claude_code provider when viewing a project.
  // Respects userStopped ref to prevent re-starting after manual Stop.
  // Note: does NOT require `leader` to exist — handleStart() creates one if needed.
  useEffect(() => {
    if (isClaudeCode && isIdle && !loading && !autoStarted.current && !userStopped.current) {
      autoStarted.current = true;
      void handleStart();
    }
  }, [isClaudeCode, isIdle, loading, projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleStart() {
    userStopped.current = false;
    setLoading(true);
    setError(null);
    try {
      const res = await api.post<{ session_id?: string }>(
        `/projects/${projectId}/leader/start`,
        { goal: goal.trim() || null },
      );
      setGoal("");
      // Update session_id immediately so the terminal can subscribe
      if (res.session_id) {
        dispatch({
          type: "SET_LEADERS",
          payload: {
            ...state.leaders,
            [projectId]: {
              ...(leader ?? {
                id: "",
                project_id: projectId,
                created_at: "",
                updated_at: "",
              }),
              status: "managing",
              session_id: res.session_id,
            } as Leader,
          },
        });
      }
      await onRefresh();
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Failed to start leader";
      setError(msg);
      console.error("Failed to start leader:", err);
    } finally {
      setLoading(false);
    }
  }

  async function handleStop() {
    // Set userStopped BEFORE any state changes to prevent the auto-start
    // useEffect from firing during the async gap or re-render.
    userStopped.current = true;
    setLoading(true);
    try {
      await api.post(`/projects/${projectId}/leader/stop`);
      autoStarted.current = true;
      // Optimistically update leader status to idle
      if (leader) {
        const updatedLeaders = {
          ...state.leaders,
          [projectId]: { ...leader, status: "idle" as const, session_id: null },
        };
        dispatch({ type: "SET_LEADERS", payload: updatedLeaders });
      }
      await onRefresh();
    } catch (err) {
      console.error("Failed to stop leader:", err);
      userStopped.current = false;
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="leader-console" data-testid="leader-console">
      <div className="leader-console__header">
        <h3>Leader</h3>
        <div className="leader-console__controls">
          {leader && <StatusBadge status={leader.status} size="sm" />}
          {!isRunning ? (
            <button
              className="btn btn-primary btn-sm"
              onClick={handleStart}
              disabled={loading}
            >
              {loading ? "Starting..." : "Start"}
            </button>
          ) : (
            <ConfirmPopover
              message="Stop the Leader session?"
              confirmLabel="Stop"
              onConfirm={handleStop}
              variant="danger"
            >
              <button className="btn btn-danger btn-sm" disabled={loading}>
                Stop
              </button>
            </ConfirmPopover>
          )}
        </div>
      </div>

      {error && (
        <div className="leader-console__error" role="alert">
          {error}
        </div>
      )}

      {!isRunning && !isClaudeCode && (
        <div className="leader-console__start-form">
          <div className="form-group">
            <label htmlFor="leader-goal">Goal (optional)</label>
            <input
              id="leader-goal"
              type="text"
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="Describe the goal for this leader..."
              onKeyDown={(e) => {
                if (e.key === "Enter") handleStart();
              }}
            />
          </div>
        </div>
      )}

      {!isRunning && isClaudeCode && loading && (
        <div className="leader-console__loading">Starting terminal...</div>
      )}

      {/* Terminal — always keep alive when running */}
      {(isRunning || (isClaudeCode && !!terminalChannel)) && (
        <div className="leader-console__terminal" ref={attachRef} />
      )}

      {/* Tab bar */}
      <div className="leader-console__tabs">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={`leader-console__tab${activeTab === tab.id ? " leader-console__tab--active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="leader-console__tab-content">
        {activeTab === "tasks" && (
          <div className="leader-console__tasks-placeholder">
            <p className="leader-console__muted">
              Task board coming soon — tasks tracked in the Tasks panel above.
            </p>
          </div>
        )}
        {activeTab === "github" && (
          <GitHubPanel projectId={projectId} />
        )}
        {activeTab === "budget" && (
          <BudgetPanel projectId={projectId} />
        )}
      </div>
    </div>
  );
}

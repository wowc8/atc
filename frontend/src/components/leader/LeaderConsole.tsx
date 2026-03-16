import { useState } from "react";
import { api } from "../../utils/api";
import { useTerminal } from "../../hooks/useTerminal";
import StatusBadge from "../common/StatusBadge";
import ConfirmPopover from "../common/ConfirmPopover";
import type { Leader } from "../../types";
import "./LeaderConsole.css";

interface LeaderConsoleProps {
  projectId: string;
  leader: Leader | undefined;
  onRefresh: () => void;
}

export default function LeaderConsole({
  projectId,
  leader,
  onRefresh,
}: LeaderConsoleProps) {
  const [goal, setGoal] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  const isRunning =
    leader?.status === "planning" || leader?.status === "managing";

  const terminalChannel = leader?.session_id
    ? `terminal:${leader.session_id}`
    : undefined;

  const { attachRef } = useTerminal({
    channel: terminalChannel,
    enabled: isRunning && !!terminalChannel,
  });

  async function handleStart() {
    setLoading(true);
    try {
      await api.post(`/projects/${projectId}/leader/start`, {
        goal: goal.trim() || null,
      });
      setGoal("");
      onRefresh();
    } catch (err) {
      console.error("Failed to start leader:", err);
    } finally {
      setLoading(false);
    }
  }

  async function handleStop() {
    setLoading(true);
    try {
      await api.post(`/projects/${projectId}/leader/stop`);
      onRefresh();
    } catch (err) {
      console.error("Failed to stop leader:", err);
    } finally {
      setLoading(false);
    }
  }

  async function handleSendMessage(e: React.FormEvent) {
    e.preventDefault();
    if (!message.trim()) return;
    try {
      await api.post(`/projects/${projectId}/leader/message`, {
        message: message.trim(),
      });
      setMessage("");
    } catch (err) {
      console.error("Failed to send message:", err);
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

      {!isRunning && (
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

      {leader?.goal && (
        <p className="leader-console__goal">{leader.goal}</p>
      )}

      {isRunning && (
        <>
          <div className="leader-console__terminal" ref={attachRef} />

          <form
            className="leader-console__input"
            onSubmit={handleSendMessage}
          >
            <input
              type="text"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="Send message to leader..."
            />
            <button
              type="submit"
              className="btn btn-sm btn-primary"
              disabled={!message.trim()}
            >
              Send
            </button>
          </form>
        </>
      )}
    </div>
  );
}

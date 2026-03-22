import { useState } from "react";
import { api } from "../../utils/api";
import { useAppContext } from "../../context/AppContext";
import AceTerminal from "./AceTerminal";
import StatusBadge from "../common/StatusBadge";
import HealthIndicator from "../common/HealthIndicator";
import ConfirmPopover from "../common/ConfirmPopover";
import type { Session } from "../../types";
import "./AceConsole.css";

interface AceConsoleProps {
  projectId: string;
  sessions: Session[];
  activeAceId: string;
  onRefresh: () => void;
  onSelectAce: (id: string) => void;
  /** Collapse back to the Ace list view */
  onCollapse: () => void;
}

export default function AceConsole({
  projectId,
  sessions,
  activeAceId,
  onRefresh,
  onSelectAce,
  onCollapse,
}: AceConsoleProps) {
  const { state } = useAppContext();
  const [loadingId, setLoadingId] = useState<string | null>(null);

  const activeSession = sessions.find((s) => s.id === activeAceId);
  const taskGraphs = state.taskGraphs[projectId] ?? [];

  function getTaskTitle(session: Session): string | null {
    const tg = taskGraphs.find((t) => t.assigned_ace_id === session.id);
    return tg ? tg.title : null;
  }

  function isRunning(s: Session) {
    return s.status === "working" || s.status === "waiting" || s.status === "connecting";
  }

  async function handleStart(sessionId: string) {
    setLoadingId(sessionId);
    try { await api.post(`/aces/${sessionId}/start`, {}); onRefresh(); }
    catch (err) { console.error("Failed to start ace:", err); }
    finally { setLoadingId(null); }
  }

  async function handleStop(sessionId: string) {
    setLoadingId(sessionId);
    try { await api.post(`/aces/${sessionId}/stop`); onRefresh(); }
    catch (err) { console.error("Failed to stop ace:", err); }
    finally { setLoadingId(null); }
  }

  async function handleDelete(sessionId: string) {
    setLoadingId(sessionId);
    try {
      await api.delete(`/aces/${sessionId}`);
      onCollapse();
      onRefresh();
    } catch (err) { console.error("Failed to delete ace:", err); }
    finally { setLoadingId(null); }
  }

  return (
    <div className="ace-console" data-testid="ace-console">
      {/* Tab bar: collapse button + one tab per Ace */}
      <div className="ace-console__tabs">
        <button
          className="ace-console__collapse-btn"
          onClick={onCollapse}
          title="Collapse to Ace list"
        >
          ↙ Collapse
        </button>
        <div className="ace-console__tab-divider" />
        {sessions.map((s) => (
          <button
            key={s.id}
            className={`ace-console__tab${activeAceId === s.id ? " ace-console__tab--active" : ""}`}
            onClick={() => onSelectAce(s.id)}
            title={s.name}
          >
            <span className="ace-console__tab-name">{s.name}</span>
            <StatusBadge status={s.status} size="sm" />
          </button>
        ))}
      </div>

      {/* Ace content */}
      {activeSession ? (
        <div className="ace-console__body">
          <div className="ace-console__header">
            <div className="ace-console__identity">
              <span className="ace-console__name">{activeSession.name}</span>
              <StatusBadge status={activeSession.status} />
              <HealthIndicator health={state.heartbeats[activeSession.id]?.health} />
              {getTaskTitle(activeSession) && (
                <span className="ace-console__task-label">
                  {getTaskTitle(activeSession)}
                </span>
              )}
            </div>
            <div className="ace-console__controls">
              {!isRunning(activeSession) ? (
                <button
                  className="btn btn-sm btn-primary"
                  onClick={() => handleStart(activeSession.id)}
                  disabled={loadingId === activeSession.id}
                >
                  {loadingId === activeSession.id ? "Starting…" : "Start"}
                </button>
              ) : (
                <ConfirmPopover
                  message={`Stop ${activeSession.name}?`}
                  confirmLabel="Stop"
                  onConfirm={() => handleStop(activeSession.id)}
                  variant="danger"
                >
                  <button className="btn btn-sm btn-danger" disabled={loadingId === activeSession.id}>
                    Stop
                  </button>
                </ConfirmPopover>
              )}
              <ConfirmPopover
                message={`Delete ${activeSession.name}? This cannot be undone.`}
                confirmLabel="Delete"
                onConfirm={() => handleDelete(activeSession.id)}
                variant="danger"
              >
                <button
                  className="btn btn-sm btn-danger"
                  disabled={loadingId === activeSession.id || isRunning(activeSession)}
                >
                  Delete
                </button>
              </ConfirmPopover>
            </div>
          </div>

          <div className="ace-console__terminal">
            <AceTerminal key={activeSession.id} session={activeSession} />
          </div>
        </div>
      ) : (
        <div className="ace-console__not-found">Ace not found.</div>
      )}
    </div>
  );
}

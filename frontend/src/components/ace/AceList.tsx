import { useState } from "react";
import { api } from "../../utils/api";
import StatusBadge from "../common/StatusBadge";
import HealthIndicator from "../common/HealthIndicator";
import ConfirmPopover from "../common/ConfirmPopover";
import AceTerminal from "./AceTerminal";
import { useAppContext } from "../../context/AppContext";
import type { Session } from "../../types";
import "./AceList.css";

interface AceListProps {
  projectId: string;
  sessions: Session[];
  onRefresh: () => void;
  onExpand?: (sessionId: string) => void;
  compact?: boolean;
}

export default function AceList({
  projectId,
  sessions,
  onRefresh,
  onExpand,
  compact = false,
}: AceListProps) {
  const { state } = useAppContext();
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const selectedSession = sessions.find((s) => s.id === selectedId);
  const taskGraphs = state.taskGraphs[projectId] ?? [];

  function getTaskTitle(session: Session): string | null {
    const tg = taskGraphs.find((t) => t.assigned_ace_id === session.id);
    return tg ? tg.title : null;
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const created = await api.post<Session>(`/projects/${projectId}/aces`, {
        name: newName.trim(),
      });
      setNewName("");
      setShowCreate(false);
      setSelectedId(created.id);
      onRefresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to create ace";
      setError(msg);
      console.error("Failed to create ace:", err);
    } finally {
      setCreating(false);
    }
  }

  async function handleStart(sessionId: string) {
    setLoadingId(sessionId);
    try {
      await api.post(`/aces/${sessionId}/start`, {});
      onRefresh();
    } catch (err) {
      console.error("Failed to start ace:", err);
    } finally {
      setLoadingId(null);
    }
  }

  async function handleStop(sessionId: string) {
    setLoadingId(sessionId);
    try {
      await api.post(`/aces/${sessionId}/stop`);
      onRefresh();
    } catch (err) {
      console.error("Failed to stop ace:", err);
    } finally {
      setLoadingId(null);
    }
  }

  async function handleDelete(sessionId: string) {
    setLoadingId(sessionId);
    try {
      await api.delete(`/aces/${sessionId}`);
      if (selectedId === sessionId) setSelectedId(null);
      onRefresh();
    } catch (err) {
      console.error("Failed to delete ace:", err);
    } finally {
      setLoadingId(null);
    }
  }

  function isRunning(s: Session) {
    return (
      s.status === "working" ||
      s.status === "waiting" ||
      s.status === "connecting"
    );
  }

  if (compact) {
    return (
      <div className="ace-list ace-list--compact" data-testid="ace-list">
        <div className="ace-list__header">
          <h3>Aces</h3>
          {sessions.length > 0 && (
            <span className="ace-list__count">{sessions.length}</span>
          )}
          <button
            className="ace-list__add-btn"
            onClick={() => setShowCreate((v) => !v)}
            title="Add ace"
          >
            {showCreate ? "✕" : "+"}
          </button>
        </div>

        {error && (
          <div className="ace-list__error" role="alert">
            {error}
          </div>
        )}

        {showCreate && (
          <form className="ace-list__create" onSubmit={handleCreate}>
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Ace name…"
              disabled={creating}
              autoFocus
            />
            <button
              type="submit"
              className="btn btn-sm btn-primary"
              disabled={creating || !newName.trim()}
            >
              {creating ? "…" : "Add"}
            </button>
          </form>
        )}

        {sessions.length === 0 ? (
          <div className="ace-list__empty">
            <span className="ace-list__empty-icon">⬡</span>
            <span>No aces yet</span>
          </div>
        ) : (
          <div className="ace-list__cards">
            {sessions.map((session) => {
              const taskTitle = getTaskTitle(session);
              return (
                <div
                  key={session.id}
                  className={`ace-list__card${isRunning(session) ? " ace-list__card--running" : ""}`}
                >
                  <div className="ace-list__card-header">
                    <div className="ace-list__card-identity">
                      <span className="ace-list__card-name">{session.name}</span>
                      <StatusBadge status={session.status} size="sm" />
                      <HealthIndicator
                        health={state.heartbeats[session.id]?.health}
                      />
                    </div>
                    {onExpand && (
                      <button
                        className="ace-list__expand-btn"
                        onClick={() => onExpand(session.id)}
                        title={`Expand ${session.name}`}
                      >
                        ⤢
                      </button>
                    )}
                  </div>

                  {taskTitle && (
                    <div className="ace-list__card-task" title={taskTitle}>
                      {taskTitle}
                    </div>
                  )}

                  <div className="ace-list__card-terminal">
                    <AceTerminal key={session.id} session={session} />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="ace-list" data-testid="ace-list">
      <div className="ace-list__header">
        <h3>Aces</h3>
        <span className="ace-list__count">{sessions.length}</span>
      </div>

      {error && (
        <div className="ace-list__error" role="alert">
          {error}
        </div>
      )}

      {/* Create new ace */}
      <form className="ace-list__create" onSubmit={handleCreate}>
        <input
          type="text"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder="New ace name..."
          disabled={creating}
        />
        <button
          type="submit"
          className="btn btn-sm btn-primary"
          disabled={creating || !newName.trim()}
        >
          {creating ? "..." : "+ Add"}
        </button>
      </form>

      {/* Session tabs */}
      {sessions.length > 0 && (
        <div className="ace-list__tabs">
          {sessions.map((session) => (
            <div
              key={session.id}
              className={`ace-list__tab ${selectedId === session.id ? "selected" : ""}`}
            >
              <button
                className="ace-list__tab-name"
                onClick={() => setSelectedId(session.id)}
              >
                <span>{session.name}</span>
                <StatusBadge status={session.status} size="sm" />
                <HealthIndicator health={state.heartbeats[session.id]?.health} />
              </button>
              <div className="ace-list__tab-actions">
                {!isRunning(session) ? (
                  <button
                    className="btn btn-sm"
                    onClick={() => handleStart(session.id)}
                    disabled={loadingId === session.id}
                    title="Start"
                  >
                    Start
                  </button>
                ) : (
                  <ConfirmPopover
                    message={`Stop ${session.name}?`}
                    confirmLabel="Stop"
                    onConfirm={() => handleStop(session.id)}
                    variant="danger"
                  >
                    <button
                      className="btn btn-sm btn-danger"
                      disabled={loadingId === session.id}
                      title="Stop"
                    >
                      Stop
                    </button>
                  </ConfirmPopover>
                )}
                <ConfirmPopover
                  message={`Delete ${session.name}? This cannot be undone.`}
                  confirmLabel="Delete"
                  onConfirm={() => handleDelete(session.id)}
                  variant="danger"
                >
                  <button
                    className="btn btn-sm btn-danger"
                    disabled={loadingId === session.id || isRunning(session)}
                    title="Delete"
                  >
                    Del
                  </button>
                </ConfirmPopover>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Terminal for selected ace */}
      {selectedSession && (
        <div className="ace-list__terminal-container">
          <AceTerminal key={selectedSession.id} session={selectedSession} />
        </div>
      )}

      {sessions.length === 0 && (
        <p className="ace-list__empty">No aces yet. Create one above.</p>
      )}
    </div>
  );
}

import { useState, useEffect } from "react";
import { api } from "../../utils/api";
import { useAppContext } from "../../context/AppContext";
import "./TowerModal.css";

interface TowerModalProps {
  open: boolean;
  onClose: () => void;
}

interface TowerMemoryEntry {
  key: string;
  value: unknown;
}

export default function TowerModal({ open, onClose }: TowerModalProps) {
  const { state } = useAppContext();
  const [memory, setMemory] = useState<TowerMemoryEntry[]>([]);
  const [goal, setGoal] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    api
      .get<TowerMemoryEntry[]>("/tower/memory")
      .then(setMemory)
      .catch(() => setMemory([]));
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function handleEscape(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [open, onClose]);

  if (!open) return null;

  async function handleSetGoal(e: React.FormEvent) {
    e.preventDefault();
    if (!goal.trim()) return;
    setSubmitting(true);
    try {
      await api.post("/tower/goal", { goal: goal.trim() });
      setGoal("");
    } catch (err) {
      console.error("Failed to set tower goal:", err);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal panel tower-modal"
        onClick={(e) => e.stopPropagation()}
        data-testid="tower-modal"
      >
        <div className="modal__header">
          <h2>Tower Control</h2>
          <button className="modal__close" onClick={onClose} aria-label="Close">
            &times;
          </button>
        </div>

        <div className="tower-modal__status">
          <div className="tower-modal__stat">
            <span className="tower-modal__stat-label">Status</span>
            <span className="tower-modal__stat-value">
              {state.brainStatus.status}
            </span>
          </div>
          <div className="tower-modal__stat">
            <span className="tower-modal__stat-label">Active Projects</span>
            <span className="tower-modal__stat-value">
              {state.brainStatus.active_projects}
            </span>
          </div>
        </div>

        <form className="tower-modal__goal-form" onSubmit={handleSetGoal}>
          <div className="form-group">
            <label htmlFor="tower-goal">Set Tower Goal</label>
            <input
              id="tower-goal"
              type="text"
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="Describe a high-level goal..."
            />
          </div>
          <button
            type="submit"
            className="btn btn-primary btn-sm"
            disabled={submitting || !goal.trim()}
          >
            {submitting ? "Setting..." : "Set Goal"}
          </button>
        </form>

        {memory.length > 0 && (
          <div className="tower-modal__memory">
            <h3>Tower Memory</h3>
            {memory.map((entry) => (
              <div key={entry.key} className="tower-modal__memory-entry">
                <span className="tower-modal__memory-key">{entry.key}</span>
                <span className="tower-modal__memory-value">
                  {typeof entry.value === "string"
                    ? entry.value
                    : JSON.stringify(entry.value)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

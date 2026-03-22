import { useState, useEffect, useCallback, useRef } from "react";
import type { ContextEntry, ContextScope } from "../../types";
import { api, ApiError } from "../../utils/api";
import ConfirmPopover from "../common/ConfirmPopover";
import "./ContextHub.css";

/** Props for the ContextHub component. */
interface ContextHubProps {
  /** Which scope tab to show initially. */
  scope: ContextScope;
  /** Project ID — required for project-scoped entries. */
  projectId?: string;
  /** Session ID — required for tower/leader/ace scoped entries. */
  sessionId?: string;
  /** Whether to show scope tabs or lock to a single scope. */
  showScopeTabs?: boolean;
  /** Which scopes to show in the tab bar (defaults to all). */
  availableScopes?: ContextScope[];
}

const SCOPE_LABELS: Record<ContextScope, string> = {
  global: "Global",
  project: "Project",
  tower: "Tower",
  leader: "Leader",
  ace: "Ace",
};

const ENTRY_TYPES = ["text", "status", "list", "link", "json"] as const;

export default function ContextHub({
  scope: initialScope,
  projectId,
  sessionId,
  showScopeTabs = false,
  availableScopes = ["global", "project", "tower", "leader", "ace"],
}: ContextHubProps) {
  const [activeScope, setActiveScope] = useState<ContextScope>(initialScope);
  const [entries, setEntries] = useState<ContextEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [showCreateForm, setShowCreateForm] = useState(false);

  // Fetch entries for the active scope
  const fetchEntries = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      let path: string;
      if (activeScope === "global") {
        path = "/context";
      } else if (activeScope === "project" && projectId) {
        path = `/projects/${projectId}/context`;
      } else if (sessionId) {
        path = `/sessions/${sessionId}/context?scope=${activeScope}`;
      } else {
        setEntries([]);
        setLoading(false);
        return;
      }
      const data = await api.get<ContextEntry[]>(path);
      setEntries(data);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Failed to load context entries";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [activeScope, projectId, sessionId]);

  useEffect(() => {
    void fetchEntries();
  }, [fetchEntries]);

  // Poll for new entries every 5s so context seeded by Tower/Leader shows up
  // without requiring a page reload.
  useEffect(() => {
    const interval = setInterval(() => {
      void fetchEntries();
    }, 5000);
    return () => clearInterval(interval);
  }, [fetchEntries]);

  // Update scope when initialScope prop changes
  useEffect(() => {
    setActiveScope(initialScope);
  }, [initialScope]);

  const handleCreate = async (data: {
    key: string;
    value: string;
    entry_type: string;
    restricted: boolean;
  }) => {
    try {
      let path: string;
      let body: Record<string, unknown> = { ...data };
      if (activeScope === "global") {
        path = "/context";
      } else if (activeScope === "project" && projectId) {
        path = `/projects/${projectId}/context`;
      } else if (sessionId) {
        path = `/sessions/${sessionId}/context`;
        body = { ...data, scope: activeScope };
      } else {
        return;
      }
      await api.post(path, body);
      setShowCreateForm(false);
      await fetchEntries();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Failed to create entry";
      setError(msg);
    }
  };

  const handleUpdate = async (
    entryId: string,
    updates: { value?: string; entry_type?: string; restricted?: boolean },
  ) => {
    try {
      await api.put(`/context/${entryId}`, updates);
      await fetchEntries();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Failed to update entry";
      setError(msg);
    }
  };

  const handleDelete = async (entryId: string) => {
    try {
      await api.delete(`/context/${entryId}`);
      setExpandedId(null);
      await fetchEntries();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Failed to delete entry";
      setError(msg);
    }
  };

  return (
    <div className="context-hub" data-testid="context-hub">
      <div className="context-hub__header">
        <h3>Context</h3>
        <button
          className="btn btn-sm btn-primary"
          onClick={() => setShowCreateForm((v) => !v)}
          data-testid="context-hub-create-btn"
        >
          {showCreateForm ? "Cancel" : "+ New"}
        </button>
      </div>

      {showScopeTabs && (
        <div className="context-hub__tabs" data-testid="context-hub-tabs">
          {availableScopes.map((s) => (
            <button
              key={s}
              className={`context-hub__tab ${s === activeScope ? "context-hub__tab--active" : ""}`}
              onClick={() => {
                setActiveScope(s);
                setExpandedId(null);
                setShowCreateForm(false);
              }}
            >
              {SCOPE_LABELS[s]}
            </button>
          ))}
        </div>
      )}

      {error && (
        <div className="context-hub__error" data-testid="context-hub-error">
          {error}
          <button className="context-hub__error-dismiss" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      {showCreateForm && (
        <CreateEntryForm
          onSubmit={handleCreate}
          onCancel={() => setShowCreateForm(false)}
        />
      )}

      {loading ? (
        <p className="context-hub__loading">Loading...</p>
      ) : entries.length === 0 ? (
        <p className="context-hub__empty">No context entries yet — they'll appear automatically when a goal is submitted.</p>
      ) : (
        <div className="context-hub__entries" data-testid="context-hub-entries">
          {entries.map((entry) => (
            <EntryCard
              key={entry.id}
              entry={entry}
              expanded={expandedId === entry.id}
              onToggle={() =>
                setExpandedId(expandedId === entry.id ? null : entry.id)
              }
              onUpdate={handleUpdate}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Entry Card — click to expand/edit inline
// ---------------------------------------------------------------------------

interface EntryCardProps {
  entry: ContextEntry;
  expanded: boolean;
  onToggle: () => void;
  onUpdate: (
    id: string,
    updates: { value?: string; entry_type?: string; restricted?: boolean },
  ) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

function EntryCard({ entry, expanded, onToggle, onUpdate, onDelete }: EntryCardProps) {
  const [editValue, setEditValue] = useState(entry.value);
  const [editType, setEditType] = useState(entry.entry_type);
  const [editRestricted, setEditRestricted] = useState(entry.restricted);
  const [saving, setSaving] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Sync local state when entry changes
  useEffect(() => {
    setEditValue(entry.value);
    setEditType(entry.entry_type);
    setEditRestricted(entry.restricted);
  }, [entry.value, entry.entry_type, entry.restricted]);

  // Auto-focus textarea when expanded
  useEffect(() => {
    if (expanded && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [expanded]);

  const hasChanges =
    editValue !== entry.value ||
    editType !== entry.entry_type ||
    editRestricted !== entry.restricted;

  const handleSave = async () => {
    if (!hasChanges) return;
    setSaving(true);
    const updates: Record<string, unknown> = {};
    if (editValue !== entry.value) updates.value = editValue;
    if (editType !== entry.entry_type) updates.entry_type = editType;
    if (editRestricted !== entry.restricted) updates.restricted = editRestricted;
    await onUpdate(entry.id, updates);
    setSaving(false);
  };

  const handleBlur = () => {
    if (hasChanges) {
      void handleSave();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      void handleSave();
    }
  };

  // Value preview — first line, truncated
  const preview =
    entry.value.length > 120
      ? entry.value.slice(0, 120) + "..."
      : entry.value.split("\n")[0];

  return (
    <div
      className={`context-hub__entry ${expanded ? "context-hub__entry--expanded" : ""} ${entry.restricted ? "context-hub__entry--restricted" : ""}`}
      data-testid="context-hub-entry"
    >
      <button
        className="context-hub__entry-header"
        onClick={onToggle}
        type="button"
      >
        <div className="context-hub__entry-meta">
          <span className="context-hub__entry-key">{entry.key}</span>
          <span className="context-hub__entry-type-badge">{entry.entry_type}</span>
          {entry.restricted && (
            <span className="context-hub__entry-restricted-badge">restricted</span>
          )}
        </div>
        {!expanded && (
          <span className="context-hub__entry-preview">{preview}</span>
        )}
      </button>

      {expanded && (
        <div className="context-hub__entry-body">
          <div className="context-hub__entry-fields">
            <textarea
              ref={textareaRef}
              className="context-hub__entry-value-input"
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              onBlur={handleBlur}
              onKeyDown={handleKeyDown}
              rows={Math.min(Math.max(editValue.split("\n").length, 3), 16)}
              data-testid="context-hub-entry-value"
            />
            <div className="context-hub__entry-row">
              <label className="context-hub__entry-field">
                <span>Type</span>
                <select
                  value={editType}
                  onChange={(e) => {
                    setEditType(e.target.value);
                  }}
                  onBlur={handleBlur}
                >
                  {ENTRY_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </label>
              <label className="context-hub__entry-field context-hub__entry-toggle">
                <input
                  type="checkbox"
                  checked={editRestricted}
                  onChange={(e) => {
                    setEditRestricted(e.target.checked);
                    // Auto-save toggle immediately
                    void onUpdate(entry.id, { restricted: e.target.checked });
                  }}
                />
                <span>Restricted</span>
              </label>
            </div>
          </div>

          <div className="context-hub__entry-actions">
            {hasChanges && (
              <button
                className="btn btn-sm btn-primary"
                onClick={() => void handleSave()}
                disabled={saving}
              >
                {saving ? "Saving..." : "Save"}
              </button>
            )}
            <ConfirmPopover
              message={`Delete "${entry.key}"?`}
              confirmLabel="Delete"
              variant="danger"
              onConfirm={() => void onDelete(entry.id)}
            >
              <button className="btn btn-sm btn-danger">Delete</button>
            </ConfirmPopover>
          </div>

          <div className="context-hub__entry-footer">
            {entry.updated_by && (
              <span className="context-hub__entry-updated-by">
                by {entry.updated_by}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create Entry Form
// ---------------------------------------------------------------------------

interface CreateEntryFormProps {
  onSubmit: (data: {
    key: string;
    value: string;
    entry_type: string;
    restricted: boolean;
  }) => Promise<void>;
  onCancel: () => void;
}

function CreateEntryForm({ onSubmit, onCancel }: CreateEntryFormProps) {
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [entryType, setEntryType] = useState("text");
  const [restricted, setRestricted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const keyRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    keyRef.current?.focus();
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!key.trim() || !value.trim()) return;
    setSubmitting(true);
    await onSubmit({ key: key.trim(), value, entry_type: entryType, restricted });
    setSubmitting(false);
  };

  return (
    <form
      className="context-hub__create-form"
      onSubmit={(e) => void handleSubmit(e)}
      data-testid="context-hub-create-form"
    >
      <div className="context-hub__create-row">
        <input
          ref={keyRef}
          className="context-hub__create-key"
          type="text"
          placeholder="Key"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          required
          data-testid="context-hub-create-key"
        />
        <select
          className="context-hub__create-type"
          value={entryType}
          onChange={(e) => setEntryType(e.target.value)}
        >
          {ENTRY_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </div>
      <textarea
        className="context-hub__create-value"
        placeholder="Value"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        rows={3}
        required
        data-testid="context-hub-create-value"
      />
      <div className="context-hub__create-actions">
        <label className="context-hub__entry-toggle">
          <input
            type="checkbox"
            checked={restricted}
            onChange={(e) => setRestricted(e.target.checked)}
          />
          <span>Restricted</span>
        </label>
        <div className="context-hub__create-buttons">
          <button type="button" className="btn btn-sm" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="submit"
            className="btn btn-sm btn-primary"
            disabled={submitting || !key.trim() || !value.trim()}
          >
            {submitting ? "Creating..." : "Create"}
          </button>
        </div>
      </div>
    </form>
  );
}

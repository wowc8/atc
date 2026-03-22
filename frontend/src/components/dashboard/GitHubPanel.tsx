import { useState } from "react";
import { api } from "../../utils/api";

interface PR {
  id: string;
  project_id: string | null;
  number: number;
  title: string | null;
  status: string | null;
  ci_status: string | null;
  url: string | null;
  updated_at: string;
}

interface Props {
  projectId: string;
}

function CiStatusBadge({ status }: { status: string | null }) {
  if (!status) return null;
  const config: Record<string, { label: string; color: string }> = {
    success: { label: "✓", color: "var(--color-status-green, #3fb950)" },
    failure: { label: "✗", color: "var(--color-status-red, #f85149)" },
    running: { label: "⟳", color: "var(--color-status-amber, #d29922)" },
    pending: { label: "○", color: "var(--color-text-muted)" },
  };
  const cfg = config[status] ?? { label: status, color: "var(--color-text-muted)" };
  return (
    <span
      title={`CI: ${status}`}
      style={{ color: cfg.color, fontSize: "14px", fontWeight: 600 }}
    >
      {cfg.label}
    </span>
  );
}

export default function GitHubPanel({ projectId }: Props) {
  const [prs, setPrs] = useState<PR[]>([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [syncing, setSyncing] = useState(false);

  async function fetchPrs() {
    setLoading(true);
    try {
      const result = await api.get<PR[]>(`/projects/${projectId}/github/prs`);
      setPrs(result);
      setLoaded(true);
    } catch {
      setPrs([]);
    } finally {
      setLoading(false);
    }
  }

  if (!loaded && !loading) {
    void fetchPrs();
  }

  async function handleSync() {
    setSyncing(true);
    try {
      await api.post(`/projects/${projectId}/github/sync`);
      setLoaded(false);
    } catch {
      /* ignore */
    } finally {
      setSyncing(false);
    }
  }

  return (
    <div className="github-panel">
      <div className="github-panel__header">
        <span className="github-panel__title">Pull Requests</span>
        <button
          className="btn btn-sm"
          onClick={handleSync}
          disabled={syncing || loading}
        >
          {syncing ? "Syncing..." : "Sync"}
        </button>
      </div>

      {loading && (
        <p className="github-panel__empty">Loading PRs...</p>
      )}

      {!loading && !prs.length && (
        <p className="github-panel__empty">No open PRs found.</p>
      )}

      {!loading && prs.length > 0 && (
        <ul className="github-panel__list">
          {prs.map((pr) => (
            <li key={pr.id} className="github-panel__pr">
              <CiStatusBadge status={pr.ci_status} />
              <span className="github-panel__pr-number">#{pr.number}</span>
              <span className="github-panel__pr-title">{pr.title ?? "(no title)"}</span>
              {pr.url && (
                <a
                  href={pr.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="github-panel__pr-link"
                  title="Open in browser"
                >
                  ↗
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

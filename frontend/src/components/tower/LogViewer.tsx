import { useState } from "react";
import { useAppContext } from "../../context/AppContext";
import { api } from "../../utils/api";
import { sendReport } from "../../utils/sentry";
import type { FailureLog } from "../../types";
import "./LogViewer.css";

interface Props {
  onClose: () => void;
}

type FilterLevel = "all" | "info" | "warning" | "error" | "critical";

function formatClaudeContext(entry: FailureLog): string {
  const lines: string[] = [
    `## Failure Log Entry`,
    ``,
    `**Level**: ${entry.level}`,
    `**Category**: ${entry.category}`,
    `**Time**: ${entry.created_at}`,
  ];

  if (entry.project_id) lines.push(`**Project ID**: ${entry.project_id}`);
  if (entry.entity_type) lines.push(`**Entity**: ${entry.entity_type}${entry.entity_id ? ` (${entry.entity_id})` : ""}`);

  lines.push(``, `### Message`, ``, entry.message);

  if (entry.context && Object.keys(entry.context).length > 0) {
    lines.push(``, `### Context`, ``, "```json", JSON.stringify(entry.context, null, 2), "```");
  }

  if (entry.stack_trace) {
    lines.push(``, `### Stack Trace`, ``, "```", entry.stack_trace, "```");
  }

  lines.push(``, `---`, `Please analyze this failure and suggest a fix.`);

  return lines.join("\n");
}

function LevelBadge({ level }: { level: string }) {
  return <span className={`log-level log-level--${level}`}>{level}</span>;
}

function LogEntry({ entry }: { entry: FailureLog }) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const [reported, setReported] = useState(false);
  const [reporting, setReporting] = useState(false);
  const { dispatch } = useAppContext();

  const handleCopy = async () => {
    await navigator.clipboard.writeText(formatClaudeContext(entry));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleSendReport = async () => {
    setReporting(true);
    const result = await sendReport(
      `[${entry.level}] ${entry.category}: ${entry.message}`,
      {
        failure_log_id: entry.id,
        category: entry.category,
        project_id: entry.project_id,
        entity_type: entry.entity_type,
        stack_trace: entry.stack_trace,
        source: "log_viewer",
      },
    );
    setReported(result.sent);
    setReporting(false);
  };

  const handleResolve = async () => {
    await api.patch<FailureLog>(`/failure-logs/${entry.id}/resolve`, {});
    dispatch({ type: "RESOLVE_FAILURE_LOG", payload: entry.id });
  };

  return (
    <div className={`log-entry ${entry.resolved ? "log-entry--resolved" : ""}`} data-testid="log-entry">
      <div className="log-entry__header" onClick={() => setExpanded(!expanded)}>
        <span className="log-entry__expand">{expanded ? "\u25BC" : "\u25B6"}</span>
        <LevelBadge level={entry.level} />
        <span className="log-entry__category">{entry.category}</span>
        <span className="log-entry__message">{entry.message}</span>
        <span className="log-entry__time">{new Date(entry.created_at).toLocaleString()}</span>
        {entry.resolved && <span className="log-entry__resolved-tag">resolved</span>}
      </div>

      {expanded && (
        <div className="log-entry__body">
          {entry.entity_type && (
            <div className="log-entry__detail">
              <strong>Entity:</strong> {entry.entity_type}
              {entry.entity_id && <> ({entry.entity_id})</>}
            </div>
          )}
          {entry.project_id && (
            <div className="log-entry__detail">
              <strong>Project:</strong> {entry.project_id}
            </div>
          )}

          {entry.context && Object.keys(entry.context).length > 0 && (
            <div className="log-entry__context">
              <strong>Context:</strong>
              <pre>{JSON.stringify(entry.context, null, 2)}</pre>
            </div>
          )}

          {entry.stack_trace && (
            <div className="log-entry__stack">
              <strong>Stack Trace:</strong>
              <pre>{entry.stack_trace}</pre>
            </div>
          )}

          <div className="log-entry__actions">
            <button
              className="log-entry__copy-btn"
              onClick={handleCopy}
              data-testid="copy-for-claude"
              title="Copy formatted error context for pasting into Claude"
            >
              {copied ? "Copied!" : "Copy for Claude"}
            </button>
            <button
              className="log-entry__report-btn"
              onClick={handleSendReport}
              disabled={reported || reporting}
              data-testid="send-report-btn"
            >
              {reported ? "Reported" : reporting ? "Sending..." : "Send Report"}
            </button>
            {!entry.resolved && (
              <button
                className="log-entry__resolve-btn"
                onClick={handleResolve}
                data-testid="resolve-btn"
              >
                Mark Resolved
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function LogViewer({ onClose }: Props) {
  const { state } = useAppContext();
  const [filter, setFilter] = useState<FilterLevel>("all");
  const [showResolved, setShowResolved] = useState(false);

  const filtered = state.failureLogs.filter((f) => {
    if (filter !== "all" && f.level !== filter) return false;
    if (!showResolved && f.resolved) return false;
    return true;
  });

  return (
    <div className="log-viewer" data-testid="log-viewer">
      <div className="log-viewer__header">
        <h2 className="log-viewer__title">Failure Logs</h2>
        <div className="log-viewer__controls">
          <select
            className="log-viewer__filter"
            value={filter}
            onChange={(e) => setFilter(e.target.value as FilterLevel)}
            data-testid="level-filter"
          >
            <option value="all">All levels</option>
            <option value="critical">Critical</option>
            <option value="error">Error</option>
            <option value="warning">Warning</option>
            <option value="info">Info</option>
          </select>
          <label className="log-viewer__checkbox">
            <input
              type="checkbox"
              checked={showResolved}
              onChange={(e) => setShowResolved(e.target.checked)}
            />
            Show resolved
          </label>
          <button className="log-viewer__close" onClick={onClose} data-testid="close-log-viewer">
            Close
          </button>
        </div>
      </div>

      <div className="log-viewer__list">
        {filtered.length === 0 ? (
          <div className="log-viewer__empty">No failure logs to display.</div>
        ) : (
          filtered.map((entry) => <LogEntry key={entry.id} entry={entry} />)
        )}
      </div>
    </div>
  );
}

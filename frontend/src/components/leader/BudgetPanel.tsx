import { useState } from "react";
import { api } from "../../utils/api";

interface Budget {
  project_id: string;
  daily_token_limit: number | null;
  monthly_cost_limit: number | null;
  warn_threshold: number;
  current_status: "ok" | "warn" | "exceeded";
  updated_at: string;
}

interface Props {
  projectId: string;
}

function StatusIndicator({ status }: { status: Budget["current_status"] }) {
  const cfg = {
    ok: { label: "OK", color: "var(--color-status-green, #3fb950)" },
    warn: { label: "Warning", color: "var(--color-status-amber, #d29922)" },
    exceeded: { label: "Exceeded", color: "var(--color-status-red, #f85149)" },
  }[status];
  return (
    <span style={{ color: cfg.color, fontWeight: 600 }}>{cfg.label}</span>
  );
}

function ProgressBar({
  value,
  max,
  status,
}: {
  value: number;
  max: number;
  status: Budget["current_status"];
}) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  const color =
    status === "exceeded"
      ? "var(--color-status-red, #f85149)"
      : status === "warn"
        ? "var(--color-status-amber, #d29922)"
        : "var(--color-accent)";
  return (
    <div
      style={{
        background: "var(--color-border)",
        borderRadius: "4px",
        height: "6px",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          width: `${pct}%`,
          height: "100%",
          background: color,
          borderRadius: "4px",
          transition: "width 0.3s",
        }}
      />
    </div>
  );
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}k`;
  return String(n);
}

export default function BudgetPanel({ projectId }: Props) {
  const [budget, setBudget] = useState<Budget | null>(null);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);

  // Form state
  const [dailyTokenLimit, setDailyTokenLimit] = useState("");
  const [monthlyCostLimit, setMonthlyCostLimit] = useState("");
  const [warnThreshold, setWarnThreshold] = useState("80");

  async function fetchBudget() {
    setLoading(true);
    try {
      const result = await api.get<Budget>(`/projects/${projectId}/budget`);
      setBudget(result);
      setDailyTokenLimit(result.daily_token_limit?.toString() ?? "");
      setMonthlyCostLimit(result.monthly_cost_limit?.toString() ?? "");
      setWarnThreshold(String(Math.round(result.warn_threshold * 100)));
      setLoaded(true);
    } catch {
      setBudget(null);
    } finally {
      setLoading(false);
    }
  }

  if (!loaded && !loading) {
    void fetchBudget();
  }

  async function handleSave() {
    setSaving(true);
    try {
      const body = {
        daily_token_limit: dailyTokenLimit ? parseInt(dailyTokenLimit, 10) : null,
        monthly_cost_limit: monthlyCostLimit ? parseFloat(monthlyCostLimit) : null,
        warn_threshold: parseInt(warnThreshold, 10) / 100,
      };
      const result = await api.put<Budget>(`/projects/${projectId}/budget`, body);
      setBudget(result);
      setEditing(false);
    } catch {
      /* ignore */
    } finally {
      setSaving(false);
    }
  }

  async function handleReset() {
    try {
      await api.post(`/projects/${projectId}/budget/reset`);
      setLoaded(false);
    } catch {
      /* ignore */
    }
  }

  if (loading) {
    return <div className="budget-panel__loading">Loading budget...</div>;
  }

  return (
    <div className="budget-panel">
      <div className="budget-panel__header">
        <span className="budget-panel__title">Budget</span>
        {budget && <StatusIndicator status={budget.current_status} />}
        <div style={{ flex: 1 }} />
        {budget?.current_status === "exceeded" && (
          <button className="btn btn-sm" onClick={handleReset}>
            Reset
          </button>
        )}
        <button
          className="btn btn-sm"
          onClick={() => setEditing((e) => !e)}
        >
          {editing ? "Cancel" : "Edit"}
        </button>
      </div>

      {budget && !editing && (
        <div className="budget-panel__stats">
          {budget.daily_token_limit !== null && (
            <div className="budget-panel__stat">
              <div className="budget-panel__stat-row">
                <span className="budget-panel__stat-label">Daily Tokens</span>
                <span className="budget-panel__stat-value">
                  {formatTokens(budget.daily_token_limit)}
                </span>
              </div>
              <ProgressBar
                value={0}
                max={budget.daily_token_limit}
                status={budget.current_status}
              />
            </div>
          )}
          {budget.monthly_cost_limit !== null && (
            <div className="budget-panel__stat">
              <div className="budget-panel__stat-row">
                <span className="budget-panel__stat-label">Monthly Cost</span>
                <span className="budget-panel__stat-value">
                  ${budget.monthly_cost_limit.toFixed(2)}
                </span>
              </div>
              <ProgressBar
                value={0}
                max={budget.monthly_cost_limit}
                status={budget.current_status}
              />
            </div>
          )}
          {budget.daily_token_limit === null &&
            budget.monthly_cost_limit === null && (
              <p className="budget-panel__no-limits">
                No limits configured. Click Edit to set limits.
              </p>
            )}
        </div>
      )}

      {editing && (
        <div className="budget-panel__form">
          <div className="form-group">
            <label htmlFor="budget-daily-tokens">Daily Token Limit</label>
            <input
              id="budget-daily-tokens"
              type="number"
              min="0"
              value={dailyTokenLimit}
              onChange={(e) => setDailyTokenLimit(e.target.value)}
              placeholder="e.g. 100000 (leave blank for no limit)"
            />
          </div>
          <div className="form-group">
            <label htmlFor="budget-monthly-cost">Monthly Cost Limit ($)</label>
            <input
              id="budget-monthly-cost"
              type="number"
              min="0"
              step="0.01"
              value={monthlyCostLimit}
              onChange={(e) => setMonthlyCostLimit(e.target.value)}
              placeholder="e.g. 50.00 (leave blank for no limit)"
            />
          </div>
          <div className="form-group">
            <label htmlFor="budget-warn-pct">
              Warning Threshold ({warnThreshold}%)
            </label>
            <input
              id="budget-warn-pct"
              type="range"
              min="10"
              max="99"
              value={warnThreshold}
              onChange={(e) => setWarnThreshold(e.target.value)}
            />
          </div>
          <button
            className="btn btn-primary btn-sm"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      )}
    </div>
  );
}

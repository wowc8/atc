import { useAppContext } from "../context/AppContext";
import "./UsagePage.css";

export default function UsagePage() {
  const { state } = useAppContext();
  const { usage, projects, budgets } = state;

  return (
    <div className="usage-page" data-testid="usage-page">
      <h1>Usage</h1>

      <div className="usage-page__grid">
        {/* Cost overview */}
        <div className="panel usage-page__card">
          <h3>Cost Overview</h3>
          <div className="usage-page__stats">
            <div className="usage-page__stat">
              <span className="usage-page__stat-value">
                ${usage.today_cost.toFixed(2)}
              </span>
              <span className="usage-page__stat-label">Today</span>
            </div>
            <div className="usage-page__stat">
              <span className="usage-page__stat-value">
                ${usage.month_cost.toFixed(2)}
              </span>
              <span className="usage-page__stat-label">This Month</span>
            </div>
          </div>
          <div className="usage-page__chart-placeholder">
            Cost chart placeholder — will use Recharts
          </div>
        </div>

        {/* Token usage */}
        <div className="panel usage-page__card">
          <h3>Token Usage</h3>
          <div className="usage-page__stats">
            <div className="usage-page__stat">
              <span className="usage-page__stat-value">
                {formatTokens(usage.today_tokens)}
              </span>
              <span className="usage-page__stat-label">Today</span>
            </div>
            <div className="usage-page__stat">
              <span className="usage-page__stat-value">
                {formatTokens(usage.month_tokens)}
              </span>
              <span className="usage-page__stat-label">This Month</span>
            </div>
          </div>
          <div className="usage-page__chart-placeholder">
            Token chart placeholder — will use Recharts
          </div>
        </div>

        {/* Budget utilization per project */}
        <div className="panel usage-page__card usage-page__card--full">
          <h3>Budget Utilization</h3>
          {projects.length === 0 ? (
            <p className="usage-page__muted">No projects yet.</p>
          ) : (
            <div className="usage-page__budget-list">
              {projects.map((project) => {
                const budget = budgets[project.id];
                return (
                  <div key={project.id} className="usage-page__budget-row">
                    <span className="usage-page__budget-name">
                      {project.name}
                    </span>
                    <div className="usage-page__budget-bar">
                      <div
                        className="usage-page__budget-fill"
                        style={{
                          width: budget
                            ? `${Math.min(budget.warn_threshold * 100, 100)}%`
                            : "0%",
                          background: budget
                            ? budget.current_status === "exceeded"
                              ? "var(--color-status-red)"
                              : budget.current_status === "warn"
                                ? "var(--color-status-amber)"
                                : "var(--color-accent)"
                            : "var(--color-text-muted)",
                        }}
                      />
                    </div>
                    <span className="usage-page__budget-status">
                      {budget?.current_status ?? "no budget"}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}k`;
  return String(n);
}

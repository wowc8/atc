import { useState } from "react";
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { useAppContext } from "../context/AppContext";
import { api } from "../utils/api";
import "./UsagePage.css";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface CostDataPoint {
  date: string;
  cost_usd: number;
}

interface TokenDataPoint {
  date: string;
  input_tokens: number;
  output_tokens: number;
  model: string;
}

interface ResourceDataPoint {
  timestamp: string;
  cpu_pct: number;
  ram_mb: number;
}

type Period = "7d" | "30d" | "90d";

const PERIODS: Period[] = ["7d", "30d", "90d"];
const PERIOD_LABELS: Record<Period, string> = {
  "7d": "7d",
  "30d": "30d",
  "90d": "90d",
};

// ---------------------------------------------------------------------------
// Period selector
// ---------------------------------------------------------------------------

function PeriodSelector({
  period,
  onChange,
}: {
  period: Period;
  onChange: (p: Period) => void;
}) {
  return (
    <div className="usage-page__period-selector">
      {PERIODS.map((p) => (
        <button
          key={p}
          className={`usage-page__period-btn${p === period ? " usage-page__period-btn--active" : ""}`}
          onClick={() => onChange(p)}
        >
          {PERIOD_LABELS[p]}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function UsagePage() {
  const { state } = useAppContext();
  const { usage, projects, budgets } = state;

  const [period, setPeriod] = useState<Period>("7d");

  // Cost data
  const [costData, setCostData] = useState<CostDataPoint[]>([]);
  const [costLoaded, setCostLoaded] = useState(false);

  // Token data
  const [tokenData, setTokenData] = useState<TokenDataPoint[]>([]);
  const [tokenLoaded, setTokenLoaded] = useState(false);

  // Resource data
  const [resourceData, setResourceData] = useState<ResourceDataPoint[]>([]);
  const [resourceLoaded, setResourceLoaded] = useState(false);

  async function fetchCost(p: Period) {
    try {
      const data = await api.get<CostDataPoint[]>(`/usage/cost?period=${p}`);
      setCostData(data);
      setCostLoaded(true);
    } catch {
      setCostData([]);
    }
  }

  async function fetchTokens(p: Period) {
    try {
      const data = await api.get<TokenDataPoint[]>(`/usage/tokens?period=${p}`);
      setTokenData(data);
      setTokenLoaded(true);
    } catch {
      setTokenData([]);
    }
  }

  async function fetchResources() {
    try {
      const data = await api.get<ResourceDataPoint[]>("/usage/resources");
      setResourceData([...data].reverse());
      setResourceLoaded(true);
    } catch {
      setResourceData([]);
    }
  }

  if (!costLoaded) void fetchCost(period);
  if (!tokenLoaded) void fetchTokens(period);
  if (!resourceLoaded) void fetchResources();

  function handlePeriodChange(p: Period) {
    setPeriod(p);
    setCostLoaded(false);
    setTokenLoaded(false);
  }

  // Pivot token data for grouped bar (one entry per date+model)
  const tokenByDate = tokenData.reduce<Record<string, Record<string, number>>>(
    (acc, d) => {
      if (!acc[d.date]) acc[d.date] = { date: d.date as unknown as number };
      acc[d.date]![`${d.model}_in`] = (acc[d.date]![`${d.model}_in`] ?? 0) + d.input_tokens;
      acc[d.date]![`${d.model}_out`] = (acc[d.date]![`${d.model}_out`] ?? 0) + d.output_tokens;
      return acc;
    },
    {},
  );
  const tokenChartData = Object.values(tokenByDate);

  const models = [...new Set(tokenData.map((d) => d.model))];
  const MODEL_COLORS: Record<string, string> = {
    "claude-opus-4-6": "#d2a8ff",
    "claude-sonnet-4-6": "#58a6ff",
    "claude-haiku-4-5": "#3fb950",
    unknown: "var(--color-text-muted)",
  };

  function fmtTime(ts: string) {
    try {
      return new Date(ts).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return ts.slice(11, 16);
    }
  }

  return (
    <div className="usage-page" data-testid="usage-page">
      <div className="usage-page__top-row">
        <h1>Usage</h1>
        <PeriodSelector period={period} onChange={handlePeriodChange} />
      </div>

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
          {costData.length > 0 ? (
            <ResponsiveContainer width="100%" height={140}>
              <AreaChart
                data={costData}
                margin={{ top: 4, right: 4, left: 0, bottom: 0 }}
              >
                <defs>
                  <linearGradient id="costGradPage" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#58a6ff" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#58a6ff" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 9, fill: "var(--color-text-muted)" }}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 9, fill: "var(--color-text-muted)" }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v: number) => `$${v.toFixed(2)}`}
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--color-surface)",
                    border: "1px solid var(--color-border)",
                    borderRadius: "6px",
                    fontSize: "11px",
                  }}
                  formatter={(v: number) => [`$${v.toFixed(4)}`, "Cost"]}
                />
                <Area
                  type="monotone"
                  dataKey="cost_usd"
                  stroke="#58a6ff"
                  fill="url(#costGradPage)"
                  strokeWidth={2}
                  dot={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="usage-page__chart-placeholder">
              No cost data for this period.
            </div>
          )}
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
          {tokenChartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={140}>
              <BarChart
                data={tokenChartData}
                margin={{ top: 4, right: 4, left: 0, bottom: 0 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 9, fill: "var(--color-text-muted)" }}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 9, fill: "var(--color-text-muted)" }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v: number) =>
                    v >= 1_000_000
                      ? `${(v / 1_000_000).toFixed(1)}M`
                      : `${(v / 1_000).toFixed(0)}k`
                  }
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--color-surface)",
                    border: "1px solid var(--color-border)",
                    borderRadius: "6px",
                    fontSize: "11px",
                  }}
                />
                <Legend wrapperStyle={{ fontSize: "10px" }} />
                {models.map((model) => (
                  <Bar
                    key={`${model}_in`}
                    dataKey={`${model}_in`}
                    name={`${model} (in)`}
                    stackId={model}
                    fill={MODEL_COLORS[model] ?? "#58a6ff"}
                    opacity={0.7}
                  />
                ))}
                {models.map((model) => (
                  <Bar
                    key={`${model}_out`}
                    dataKey={`${model}_out`}
                    name={`${model} (out)`}
                    stackId={model}
                    fill={MODEL_COLORS[model] ?? "#58a6ff"}
                  />
                ))}
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="usage-page__chart-placeholder">
              No token data for this period.
            </div>
          )}
        </div>

        {/* CPU/RAM */}
        <div className="panel usage-page__card">
          <h3>CPU / RAM</h3>
          {resourceData.length > 0 ? (
            <ResponsiveContainer width="100%" height={160}>
              <LineChart
                data={resourceData}
                margin={{ top: 4, right: 4, left: 0, bottom: 0 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                <XAxis
                  dataKey="timestamp"
                  tickFormatter={fmtTime}
                  tick={{ fontSize: 9, fill: "var(--color-text-muted)" }}
                  tickLine={false}
                  interval="preserveStartEnd"
                />
                <YAxis
                  yAxisId="cpu"
                  orientation="left"
                  tick={{ fontSize: 9, fill: "var(--color-text-muted)" }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v: number) => `${v.toFixed(0)}%`}
                  domain={[0, 100]}
                />
                <YAxis
                  yAxisId="ram"
                  orientation="right"
                  tick={{ fontSize: 9, fill: "var(--color-text-muted)" }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v: number) =>
                    v >= 1024
                      ? `${(v / 1024).toFixed(1)}G`
                      : `${v.toFixed(0)}M`
                  }
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--color-surface)",
                    border: "1px solid var(--color-border)",
                    borderRadius: "6px",
                    fontSize: "11px",
                  }}
                  labelFormatter={fmtTime}
                />
                <Legend
                  formatter={(v: string) =>
                    v === "cpu_pct" ? "CPU %" : "RAM MB"
                  }
                  wrapperStyle={{ fontSize: "10px" }}
                />
                <Line
                  yAxisId="cpu"
                  type="monotone"
                  dataKey="cpu_pct"
                  stroke="#3fb950"
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  yAxisId="ram"
                  type="monotone"
                  dataKey="ram_mb"
                  stroke="#f78166"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="usage-page__chart-placeholder">
              No resource data yet.
            </div>
          )}
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

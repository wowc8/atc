import { useState } from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { api } from "../../utils/api";
import { useAppContext } from "../../context/AppContext";

interface CostDataPoint {
  date: string;
  cost_usd: number;
}

type Period = "7d" | "30d" | "90d";

const PERIODS: Period[] = ["7d", "30d", "90d"];
const PERIOD_LABELS: Record<Period, string> = {
  "7d": "7 days",
  "30d": "30 days",
  "90d": "90 days",
};

const PROJECT_COLORS = [
  "#58a6ff",
  "#3fb950",
  "#f78166",
  "#d2a8ff",
  "#ffa657",
  "#79c0ff",
];

interface Props {
  projectId?: string;
}

export default function CostChart({ projectId }: Props) {
  const { state } = useAppContext();
  const [period, setPeriod] = useState<Period>("7d");
  const [data, setData] = useState<CostDataPoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);

  async function fetchData(p: Period) {
    setLoading(true);
    try {
      const params = new URLSearchParams({ period: p });
      if (projectId) params.set("project_id", projectId);
      const result = await api.get<CostDataPoint[]>(`/usage/cost?${params}`);
      setData(result);
      setLoaded(true);
    } catch {
      setData([]);
    } finally {
      setLoading(false);
    }
  }

  // Fetch on mount and when period/project changes
  if (!loaded && !loading) {
    void fetchData(period);
  }

  function handlePeriodChange(p: Period) {
    setPeriod(p);
    setLoaded(false);
  }

  if (loading) {
    return (
      <div className="chart-loading">
        <span className="chart-loading__text">Loading cost data...</span>
      </div>
    );
  }

  if (!data.length) {
    return (
      <div className="chart-empty">
        <div className="chart-period-selector">
          {PERIODS.map((p) => (
            <button
              key={p}
              className={`chart-period-btn${period === p ? " chart-period-btn--active" : ""}`}
              onClick={() => handlePeriodChange(p)}
            >
              {PERIOD_LABELS[p]}
            </button>
          ))}
        </div>
        <p className="chart-empty__text">No cost data for this period.</p>
      </div>
    );
  }

  return (
    <div className="chart-container">
      <div className="chart-period-selector">
        {PERIODS.map((p) => (
          <button
            key={p}
            className={`chart-period-btn${period === p ? " chart-period-btn--active" : ""}`}
            onClick={() => handlePeriodChange(p)}
          >
            {PERIOD_LABELS[p]}
          </button>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <AreaChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="costGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#58a6ff" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#58a6ff" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: "var(--color-text-muted)" }}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 10, fill: "var(--color-text-muted)" }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v: number) => `$${v.toFixed(2)}`}
          />
          <Tooltip
            contentStyle={{
              background: "var(--color-surface)",
              border: "1px solid var(--color-border)",
              borderRadius: "6px",
              fontSize: "12px",
            }}
            formatter={(v: number) => [`$${v.toFixed(4)}`, "Cost"]}
          />
          <Area
            type="monotone"
            dataKey="cost_usd"
            stroke="#58a6ff"
            fill="url(#costGrad)"
            strokeWidth={2}
            dot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

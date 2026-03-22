import { useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { api } from "../../utils/api";

interface ResourceDataPoint {
  timestamp: string;
  cpu_pct: number;
  ram_mb: number;
}

interface Props {
  projectId?: string;
}

export default function ResourceChart({ projectId }: Props) {
  const [data, setData] = useState<ResourceDataPoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);

  async function fetchData() {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (projectId) params.set("project_id", projectId);
      const result = await api.get<ResourceDataPoint[]>(
        `/usage/resources?${params}`,
      );
      // Reverse so oldest first for the chart
      setData([...result].reverse());
      setLoaded(true);
    } catch {
      setData([]);
    } finally {
      setLoading(false);
    }
  }

  if (!loaded && !loading) {
    void fetchData();
  }

  if (loading) {
    return (
      <div className="chart-loading">
        <span className="chart-loading__text">Loading resource data...</span>
      </div>
    );
  }

  if (!data.length) {
    return (
      <div className="chart-empty">
        <p className="chart-empty__text">No resource data yet.</p>
      </div>
    );
  }

  // Format timestamp for x-axis (HH:MM)
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
    <div className="chart-container">
      <ResponsiveContainer width="100%" height={180}>
        <LineChart
          data={data}
          margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
          <XAxis
            dataKey="timestamp"
            tickFormatter={fmtTime}
            tick={{ fontSize: 10, fill: "var(--color-text-muted)" }}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            yAxisId="cpu"
            orientation="left"
            tick={{ fontSize: 10, fill: "var(--color-text-muted)" }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v: number) => `${v.toFixed(0)}%`}
            domain={[0, 100]}
          />
          <YAxis
            yAxisId="ram"
            orientation="right"
            tick={{ fontSize: 10, fill: "var(--color-text-muted)" }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v: number) =>
              v >= 1024 ? `${(v / 1024).toFixed(1)}G` : `${v.toFixed(0)}M`
            }
          />
          <Tooltip
            contentStyle={{
              background: "var(--color-surface)",
              border: "1px solid var(--color-border)",
              borderRadius: "6px",
              fontSize: "12px",
            }}
            labelFormatter={fmtTime}
            formatter={(value: number, name: string) => [
              name === "cpu_pct"
                ? `${value.toFixed(1)}%`
                : `${value.toFixed(0)} MB`,
              name === "cpu_pct" ? "CPU" : "RAM",
            ]}
          />
          <Legend
            formatter={(v: string) => (v === "cpu_pct" ? "CPU %" : "RAM MB")}
            wrapperStyle={{ fontSize: "11px" }}
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
    </div>
  );
}

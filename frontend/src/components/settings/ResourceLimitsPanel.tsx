import { useState, useEffect } from "react";
import { api, ApiError } from "../../utils/api";

interface ResourceLimits {
  max_concurrent_aces: number;
  cpu_throttle_threshold: number;
  ram_throttle_threshold: number;
  cpu_pause_threshold: number;
  ram_pause_threshold: number;
}

export function ResourceLimitsPanel() {
  const [limits, setLimits] = useState<ResourceLimits | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.get<ResourceLimits>("/settings/resource-limits")
      .then(setLimits)
      .catch(() => setError("Failed to load resource limits"));
  }, []);

  const handleSave = async () => {
    if (!limits) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await api.put<ResourceLimits>("/settings/resource-limits", limits);
      setLimits(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  if (!limits) return <p className="settings-pane__description">Loading...</p>;

  return (
    <section className="panel settings-pane__section">
      <h3>Resource Limits</h3>
      <p className="settings-pane__description">
        Control how many Ace workers ATC can run simultaneously and when to
        throttle based on CPU / RAM pressure.
      </p>

      {error && <p className="settings-pane__error">{error}</p>}

      <div className="form-group">
        <label htmlFor="max-aces">Max concurrent Aces</label>
        <input
          id="max-aces"
          type="number"
          min={1}
          max={20}
          value={limits.max_concurrent_aces}
          onChange={(e) =>
            setLimits({ ...limits, max_concurrent_aces: parseInt(e.target.value) || 1 })
          }
        />
        <span className="form-hint">
          Hard cap across all projects. Recommended: 2–4 on a dev laptop.
        </span>
      </div>

      <div className="form-group">
        <label htmlFor="cpu-throttle">CPU throttle threshold (%)</label>
        <input
          id="cpu-throttle"
          type="number"
          min={10}
          max={100}
          value={limits.cpu_throttle_threshold}
          onChange={(e) =>
            setLimits({ ...limits, cpu_throttle_threshold: parseFloat(e.target.value) || 70 })
          }
        />
        <span className="form-hint">
          Above this CPU %, halve the Ace limit. Default: 70%.
        </span>
      </div>

      <div className="form-group">
        <label htmlFor="ram-throttle">RAM throttle threshold (%)</label>
        <input
          id="ram-throttle"
          type="number"
          min={10}
          max={100}
          value={limits.ram_throttle_threshold}
          onChange={(e) =>
            setLimits({ ...limits, ram_throttle_threshold: parseFloat(e.target.value) || 75 })
          }
        />
        <span className="form-hint">
          Above this RAM %, halve the Ace limit. Default: 75%.
        </span>
      </div>

      <div className="form-group">
        <label htmlFor="cpu-pause">CPU pause threshold (%)</label>
        <input
          id="cpu-pause"
          type="number"
          min={10}
          max={100}
          value={limits.cpu_pause_threshold}
          onChange={(e) =>
            setLimits({ ...limits, cpu_pause_threshold: parseFloat(e.target.value) || 85 })
          }
        />
        <span className="form-hint">
          Above this CPU %, stop spawning Aces entirely. Default: 85%.
        </span>
      </div>

      <div className="form-group">
        <label htmlFor="ram-pause">RAM pause threshold (%)</label>
        <input
          id="ram-pause"
          type="number"
          min={10}
          max={100}
          value={limits.ram_pause_threshold}
          onChange={(e) =>
            setLimits({ ...limits, ram_pause_threshold: parseFloat(e.target.value) || 90 })
          }
        />
        <span className="form-hint">
          Above this RAM %, stop spawning Aces entirely. Default: 90%.
        </span>
      </div>

      <div className="settings-pane__actions">
        <button
          className="btn btn-primary"
          onClick={() => void handleSave()}
          disabled={saving}
        >
          {saving ? "Saving..." : saved ? "Saved ✓" : "Save"}
        </button>
      </div>
    </section>
  );
}

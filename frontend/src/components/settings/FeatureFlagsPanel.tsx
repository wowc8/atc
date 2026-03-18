import { useCallback, useState } from "react";
import { useFeatureFlags } from "../../hooks/useFeatureFlags";
import { api } from "../../utils/api";
import type { FeatureFlag } from "../../types";
import "./FeatureFlagsPanel.css";

const DEFAULT_FLAGS: { key: string; name: string; description: string }[] = [
  {
    key: "remote_aces",
    name: "Remote Aces",
    description: "Enable remote ace sessions on external machines.",
  },
  {
    key: "tower_memory",
    name: "Tower Memory",
    description: "Persistent memory for the Tower controller.",
  },
  {
    key: "cdp_view",
    name: "CDP View",
    description: "Chrome DevTools Protocol live view for sessions.",
  },
];

export function FeatureFlagsPanel() {
  const { flags, loading, error, toggleFlag, refetch } = useFeatureFlags();
  const [seeding, setSeeding] = useState(false);

  const handleToggle = useCallback(
    async (flag: FeatureFlag) => {
      await toggleFlag(flag.key, !flag.enabled);
    },
    [toggleFlag],
  );

  const handleSeedDefaults = useCallback(async () => {
    setSeeding(true);
    try {
      const existing = new Set(flags.map((f) => f.key));
      for (const def of DEFAULT_FLAGS) {
        if (!existing.has(def.key)) {
          await api.post("/feature-flags", {
            key: def.key,
            name: def.name,
            description: def.description,
            enabled: false,
          });
        }
      }
      await refetch();
    } finally {
      setSeeding(false);
    }
  }, [flags, refetch]);

  return (
    <section
      className="panel settings-page__section"
      data-testid="feature-flags-section"
    >
      <h2>Feature Flags</h2>
      <p className="settings-page__description">
        Toggle experimental features on or off. Disabled flags hide incomplete
        functionality from the UI.
      </p>

      {error && (
        <p className="settings-page__error" data-testid="feature-flags-error">
          {error}
        </p>
      )}

      {loading && flags.length === 0 && (
        <p className="settings-page__description">Loading...</p>
      )}

      {!loading && flags.length === 0 && (
        <div className="feature-flags__empty">
          <p className="settings-page__description">
            No feature flags configured.
          </p>
          <button
            className="btn"
            onClick={handleSeedDefaults}
            disabled={seeding}
            data-testid="seed-flags-btn"
          >
            {seeding ? "Creating..." : "Create Default Flags"}
          </button>
        </div>
      )}

      {flags.length > 0 && (
        <div className="feature-flags__list">
          {flags.map((flag) => (
            <div key={flag.id} className="feature-flags__item">
              <label className="feature-flags__toggle">
                <input
                  type="checkbox"
                  checked={flag.enabled}
                  onChange={() => handleToggle(flag)}
                  data-testid={`feature-flag-toggle-${flag.key}`}
                />
                <span className="feature-flags__name">{flag.name}</span>
              </label>
              {flag.description && (
                <p className="feature-flags__description">
                  {flag.description}
                </p>
              )}
              <code className="feature-flags__key">{flag.key}</code>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

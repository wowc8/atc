import { useCallback, useEffect, useState } from "react";
import { api } from "../utils/api";
import type { FeatureFlag } from "../types";

/**
 * Hook to manage feature flags.
 *
 * Usage:
 *   const { flags, isEnabled, toggleFlag } = useFeatureFlags();
 *   if (isEnabled("remote_aces")) { ... }
 */
export function useFeatureFlags() {
  const [flags, setFlags] = useState<FeatureFlag[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchFlags = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.get<FeatureFlag[]>("/feature-flags");
      setFlags(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load feature flags");
    } finally {
      setLoading(false);
    }
  }, []);

  const toggleFlag = useCallback(async (key: string, enabled: boolean) => {
    try {
      const updated = await api.put<FeatureFlag>(`/feature-flags/${key}`, {
        enabled,
      });
      setFlags((prev) => prev.map((f) => (f.key === key ? updated : f)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update flag");
    }
  }, []);

  const isEnabled = useCallback(
    (key: string): boolean => {
      const flag = flags.find((f) => f.key === key);
      return flag?.enabled ?? false;
    },
    [flags],
  );

  useEffect(() => {
    fetchFlags();
  }, [fetchFlags]);

  return { flags, loading, error, toggleFlag, isEnabled, refetch: fetchFlags };
}

/**
 * Convenience hook to check a single feature flag.
 *
 * Usage:
 *   const enabled = useFeatureFlag("remote_aces");
 */
export function useFeatureFlag(key: string): boolean {
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    api
      .get<FeatureFlag>(`/feature-flags/${key}`)
      .then((flag) => setEnabled(flag.enabled))
      .catch(() => setEnabled(false));
  }, [key]);

  return enabled;
}

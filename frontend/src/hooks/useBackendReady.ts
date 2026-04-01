import { useEffect, useRef, useState } from "react";

const BASE_URL =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window
    ? "http://127.0.0.1:8420"
    : "";

const INITIAL_INTERVAL_MS = 1_000;
const MAX_INTERVAL_MS = 5_000;
const MAX_ATTEMPTS = 60;

export interface BackendReadyState {
  ready: boolean;
  error: string | null;
  attempt: number;
}

export function useBackendReady(enabled: boolean): BackendReadyState {
  const [state, setState] = useState<BackendReadyState>({
    ready: false,
    error: null,
    attempt: 0,
  });

  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearTimeout(timerRef.current);
    };
  }, []);

  useEffect(() => {
    if (!enabled) {
      setState({ ready: true, error: null, attempt: 0 });
      return;
    }

    let attempt = 0;

    async function poll(): Promise<void> {
      if (!mountedRef.current) return;

      attempt += 1;
      setState((prev) => ({ ...prev, attempt }));

      try {
        const res = await fetch(`${BASE_URL}/api/health`, { signal: AbortSignal.timeout(4_000) });
        if (res.ok) {
          const body = (await res.json()) as { status?: string };
          if (body.status === "ok" || body.status === "degraded") {
            if (mountedRef.current) {
              setState({ ready: true, error: null, attempt });
            }
            return;
          }
        }
      } catch {
        // Network error — keep polling
      }

      if (!mountedRef.current) return;

      if (attempt >= MAX_ATTEMPTS) {
        setState({ ready: false, error: "Backend did not start in time. Please restart ATC.", attempt });
        return;
      }

      const delay = Math.min(INITIAL_INTERVAL_MS * attempt, MAX_INTERVAL_MS);
      timerRef.current = setTimeout(() => void poll(), delay);
    }

    void poll();

    return () => {
      clearTimeout(timerRef.current);
    };
  }, [enabled]);

  return state;
}

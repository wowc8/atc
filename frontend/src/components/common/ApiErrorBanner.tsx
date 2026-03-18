/**
 * Inline error banner that maps ATC domain error codes to actionable UI.
 *
 * Usage:
 *   <ApiErrorBanner error={apiError} onRetry={refetch} onDismiss={clearError} />
 *
 * Renders contextual actions based on error code:
 *   - session_stale  → "Reconnect" button
 *   - github_rate_limited → retry countdown timer
 *   - github_auth_failed → "Re-authenticate" button
 *   - everything else → dismissable message
 */

import { useEffect, useState } from "react";
import { ApiError } from "../../utils/api";
import { getErrorAction, getErrorTitle } from "../../utils/errors";

interface Props {
  error: ApiError | Error | null;
  /** Called when the user clicks Retry / Reconnect. */
  onRetry?: () => void;
  /** Called when the user dismisses the banner. */
  onDismiss?: () => void;
}

export default function ApiErrorBanner({ error, onRetry, onDismiss }: Props) {
  const [countdown, setCountdown] = useState<number | null>(null);

  const code = error instanceof ApiError ? error.code : null;
  const extra = error instanceof ApiError ? error.extra : {};
  const action = code ? getErrorAction(code, extra) : { type: "dismiss" as const };
  const retryAfter = action.type === "retry" ? action.retryAfter : undefined;

  // Auto-retry countdown for rate-limited errors
  useEffect(() => {
    if (action.type !== "retry" || !retryAfter) return;
    setCountdown(retryAfter);
    const interval = setInterval(() => {
      setCountdown((prev) => {
        if (prev === null || prev <= 1) {
          clearInterval(interval);
          onRetry?.();
          return null;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [action.type, retryAfter, onRetry]);

  if (!error) return null;

  const title = code ? getErrorTitle(code) : "Error";
  const message = error.message;

  return (
    <div style={styles.banner} role="alert">
      <div style={styles.content}>
        <strong style={styles.title}>{title}</strong>
        <span style={styles.message}>{message}</span>
      </div>
      <div style={styles.actions}>
        {action.type === "reconnect" && (
          <button style={styles.actionBtn} onClick={onRetry}>
            Reconnect
          </button>
        )}
        {action.type === "retry" && (
          <button
            style={styles.actionBtn}
            onClick={onRetry}
            disabled={countdown !== null}
          >
            {countdown !== null ? `Retry in ${countdown}s` : "Retry"}
          </button>
        )}
        {action.type === "relogin" && (
          <button
            style={styles.actionBtn}
            onClick={() => {
              window.location.href = "/settings";
            }}
          >
            Re-authenticate
          </button>
        )}
        {onDismiss && (
          <button style={styles.dismissBtn} onClick={onDismiss} aria-label="Dismiss">
            &times;
          </button>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  banner: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: "0.75rem",
    padding: "0.625rem 1rem",
    borderRadius: 6,
    background: "var(--color-bg-raised, #1a1a1a)",
    border: "1px solid var(--color-status-red, #ef4444)",
    fontSize: "0.8125rem",
  },
  content: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "0.125rem",
    minWidth: 0,
  },
  title: {
    color: "var(--color-status-red, #ef4444)",
    fontSize: "0.8125rem",
  },
  message: {
    color: "var(--color-text-secondary, #999)",
    fontSize: "0.75rem",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
  },
  actions: {
    display: "flex",
    alignItems: "center",
    gap: "0.5rem",
    flexShrink: 0,
  },
  actionBtn: {
    padding: "0.25rem 0.75rem",
    fontSize: "0.75rem",
    borderRadius: 4,
    border: "1px solid var(--color-accent, #3b82f6)",
    background: "var(--color-accent, #3b82f6)",
    color: "#fff",
    cursor: "pointer",
    whiteSpace: "nowrap" as const,
  },
  dismissBtn: {
    padding: "0.125rem 0.375rem",
    fontSize: "1rem",
    lineHeight: 1,
    borderRadius: 4,
    border: "none",
    background: "transparent",
    color: "var(--color-text-secondary, #999)",
    cursor: "pointer",
  },
};

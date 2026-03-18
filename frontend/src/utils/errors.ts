/**
 * Domain error types and mapping for ATC frontend.
 *
 * The backend returns structured JSON errors:
 *   { error: { code: string, message: string, extra?: Record<string, unknown> } }
 *
 * This module parses those responses and maps error codes to user-facing
 * actions (reconnect, retry, re-login, etc.).
 */

// ---------------------------------------------------------------------------
// Error codes (mirrors backend atc.core.errors)
// ---------------------------------------------------------------------------

export const ErrorCode = {
  // Agent / session
  SESSION_NOT_FOUND: "session_not_found",
  SESSION_STALE: "session_stale",
  CREATION_FAILED: "creation_failed",

  // Budget
  BUDGET_LIMIT_EXCEEDED: "budget_limit_exceeded",
  NO_BUDGET_SET: "no_budget_set",

  // GitHub
  GITHUB_RATE_LIMITED: "github_rate_limited",
  GITHUB_AUTH_FAILED: "github_auth_failed",
} as const;

export type ErrorCodeValue = (typeof ErrorCode)[keyof typeof ErrorCode];

// ---------------------------------------------------------------------------
// Structured error envelope
// ---------------------------------------------------------------------------

export interface ATCErrorBody {
  error: {
    code: string;
    message: string;
    extra?: Record<string, unknown>;
  };
}

/** Action the UI should present to the user. */
export type ErrorAction =
  | { type: "reconnect"; sessionId?: string }
  | { type: "retry"; retryAfter?: number }
  | { type: "relogin" }
  | { type: "dismiss" };

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------

/**
 * Try to parse a structured ATC error from an API response body string.
 * Returns `null` if the body isn't in the expected format.
 */
export function parseATCError(body: string): ATCErrorBody | null {
  try {
    const parsed = JSON.parse(body);
    if (parsed?.error?.code && parsed?.error?.message) {
      return parsed as ATCErrorBody;
    }
  } catch {
    // Not JSON — fall through
  }
  return null;
}

// ---------------------------------------------------------------------------
// Code → action mapping
// ---------------------------------------------------------------------------

/**
 * Map an error code to the recommended UI action.
 */
export function getErrorAction(
  code: string,
  extra?: Record<string, unknown>,
): ErrorAction {
  switch (code) {
    case ErrorCode.SESSION_STALE:
      return {
        type: "reconnect",
        sessionId: extra?.session_id as string | undefined,
      };

    case ErrorCode.GITHUB_RATE_LIMITED:
      return {
        type: "retry",
        retryAfter: (extra?.retry_after as number) ?? 60,
      };

    case ErrorCode.GITHUB_AUTH_FAILED:
      return { type: "relogin" };

    case ErrorCode.BUDGET_LIMIT_EXCEEDED:
      return { type: "dismiss" };

    default:
      return { type: "dismiss" };
  }
}

// ---------------------------------------------------------------------------
// Human-readable labels
// ---------------------------------------------------------------------------

const ERROR_TITLES: Record<string, string> = {
  [ErrorCode.SESSION_NOT_FOUND]: "Session Not Found",
  [ErrorCode.SESSION_STALE]: "Session Out of Sync",
  [ErrorCode.CREATION_FAILED]: "Session Creation Failed",
  [ErrorCode.BUDGET_LIMIT_EXCEEDED]: "Budget Limit Exceeded",
  [ErrorCode.NO_BUDGET_SET]: "No Budget Configured",
  [ErrorCode.GITHUB_RATE_LIMITED]: "GitHub Rate Limited",
  [ErrorCode.GITHUB_AUTH_FAILED]: "GitHub Auth Failed",
};

/** Get a human-friendly title for an error code. Falls back to the code itself. */
export function getErrorTitle(code: string): string {
  return ERROR_TITLES[code] ?? code;
}

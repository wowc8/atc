/**
 * Sentry SDK initialisation and helpers for ATC frontend.
 *
 * Sentry is opt-in — it only activates when the user gives consent and
 * a DSN is provided via the VITE_SENTRY_DSN env var.
 */

/// <reference types="vite/client" />

import * as Sentry from "@sentry/react";

const CONSENT_KEY = "atc:sentry_consent";

/** Whether the user has opted in to crash reporting. */
export function hasConsent(): boolean {
  return localStorage.getItem(CONSENT_KEY) === "true";
}

/** Set the user's crash-reporting consent preference. */
export function setConsent(value: boolean): void {
  localStorage.setItem(CONSENT_KEY, value ? "true" : "false");
  if (!value && Sentry.isInitialized()) {
    Sentry.getCurrentScope().setClient(undefined);
  }
}

/** Initialise Sentry if consent is given and a DSN is available. */
export function initSentry(): void {
  const dsn = import.meta.env.VITE_SENTRY_DSN as string | undefined;
  if (!dsn || !hasConsent()) return;

  Sentry.init({
    dsn,
    environment: import.meta.env.MODE as string,
    sendDefaultPii: false,
    beforeSend(event) {
      return stripPii(event);
    },
  });
}

/** Send a user-initiated report via the backend API. */
export async function sendReport(
  message: string,
  context?: Record<string, unknown>,
): Promise<{ sent: boolean; event_id?: string }> {
  try {
    const res = await fetch("/api/settings/sentry/send-report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, context }),
    });
    if (!res.ok) return { sent: false };
    return res.json();
  } catch {
    return { sent: false };
  }
}

/** Capture a frontend exception to Sentry (client-side). */
export function captureException(
  error: unknown,
  extra?: Record<string, unknown>,
): void {
  if (!Sentry.isInitialized()) return;
  Sentry.withScope((scope) => {
    if (extra) {
      Object.entries(extra).forEach(([k, v]) => scope.setExtra(k, v));
    }
    Sentry.captureException(error);
  });
}

// ---------------------------------------------------------------------------
// PII stripping
// ---------------------------------------------------------------------------

const PII_KEYS =
  /password|secret|token|api.?key|auth|credential|cookie|session.?id|email|phone|ssn|credit.?card|card.?number/i;

function stripPii(event: Sentry.ErrorEvent): Sentry.ErrorEvent {
  if (event.request) {
    if (event.request.headers) {
      event.request.headers = redactHeaders(event.request.headers);
    }
    if (event.request.data) {
      event.request.data = redactObj(
        event.request.data as Record<string, unknown>,
      ) as unknown as Sentry.ErrorEvent["request"] extends { data?: infer D }
        ? D
        : never;
    }
    if (event.request.cookies) {
      event.request.cookies = { _: "[Filtered]" };
    }
    if (event.request.query_string) {
      event.request.query_string = "";
    }
  }

  if (event.extra) {
    event.extra = redactObj(event.extra);
  }

  if (event.user) {
    event.user = { id: event.user.id ?? "anon" };
  }

  return event;
}

function redactHeaders(
  headers: Record<string, string>,
): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [k, v] of Object.entries(headers)) {
    result[k] = PII_KEYS.test(k) ? "[Filtered]" : v;
  }
  return result;
}

function redactObj(obj: unknown): Record<string, unknown> {
  if (typeof obj !== "object" || obj === null) return {};
  const result: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
    if (PII_KEYS.test(k)) {
      result[k] = "[Filtered]";
    } else if (typeof v === "object" && v !== null) {
      result[k] = redactObj(v);
    } else {
      result[k] = v;
    }
  }
  return result;
}

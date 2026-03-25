/**
 * Lightweight API client for ATC backend.
 * In dev: fetches go through the Vite proxy (/api → backend).
 * In Tauri: loaded from file://, so we need the absolute backend URL.
 */

const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
const BASE = isTauri ? "http://127.0.0.1:8420/api" : "/api";

/** Default request timeout in milliseconds (30 seconds). */
const REQUEST_TIMEOUT_MS = 30_000;

export class ApiError extends Error {
  /** Machine-readable error code from the backend (e.g. ``session_stale``). */
  public code: string | null;
  /** Extra context from the backend (e.g. ``retry_after``). */
  public extra: Record<string, unknown>;

  constructor(
    public status: number,
    message: string,
    code: string | null = null,
    extra: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.extra = extra;
  }
}

/** Try to parse a structured ATC error body and throw an enriched ApiError. */
function throwIfStructured(status: number, text: string): void {
  try {
    const parsed = JSON.parse(text);
    if (parsed?.error?.code) {
      throw new ApiError(
        status,
        parsed.error.message ?? text,
        parsed.error.code,
        parsed.error.extra ?? {},
      );
    }
  } catch (e) {
    if (e instanceof ApiError) throw e;
    // Not structured JSON — caller will throw a plain ApiError
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const res = await fetch(`${BASE}${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
      },
      ...options,
      signal: options.signal ?? controller.signal,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText);
      throwIfStructured(res.status, text);
      throw new ApiError(res.status, text);
    }
    if (res.status === 204) return undefined as T;
    return res.json() as Promise<T>;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(0, "Request timed out — is the backend running?");
    }
    throw new ApiError(0, "Network error — could not reach the server");
  } finally {
    clearTimeout(timeout);
  }
}

export const api = {
  get: <T>(path: string) => request<T>(path),

  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),

  put: <T>(path: string, body: unknown) =>
    request<T>(path, {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  patch: <T>(path: string, body: unknown) =>
    request<T>(path, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),

  /** POST that returns a Blob (for zip downloads). */
  postBlob: async (path: string, body?: unknown): Promise<Blob> => {
    const res = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText);
      throwIfStructured(res.status, text);
      throw new ApiError(res.status, text);
    }
    return res.blob();
  },
};

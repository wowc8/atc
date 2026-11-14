import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useBackendReady } from "../useBackendReady";

const invoke = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

afterEach(() => {
  vi.unstubAllGlobals();
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
  vi.clearAllMocks();
});

describe("desktop backend readiness", () => {
  it("surfaces a persisted spawn/exit error without waiting for the health timeout", async () => {
    Object.assign(window, { __TAURI_INTERNALS__: {} });
    invoke.mockResolvedValue("Backend exited (code 3). See ~/.atc/logs/desktop-backend.log");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useBackendReady(true));
    await waitFor(() => expect(result.current.error).toContain("Backend exited"));
    expect(result.current.ready).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("still requires real health readiness when the sidecar has no failure", async () => {
    Object.assign(window, { __TAURI_INTERNALS__: {} });
    invoke.mockResolvedValue(null);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: "ok" }) }));
    const { result } = renderHook(() => useBackendReady(true));
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.error).toBeNull();
  });

  it("does not invoke desktop commands when readiness is disabled", async () => {
    const { result } = renderHook(() => useBackendReady(false));
    await act(async () => {});
    expect(result.current.ready).toBe(true);
    expect(invoke).not.toHaveBeenCalled();
  });
});

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useWebSocket } from "./useWebSocket";

// ---------------------------------------------------------------------------
// Mock WebSocket
// ---------------------------------------------------------------------------
class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  url: string;
  readyState: number = MockWebSocket.CONNECTING;
  onopen: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  send = vi.fn();
  close = vi.fn().mockImplementation(() => {
    if (this.readyState === MockWebSocket.CLOSED) return;
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.(new CloseEvent("close"));
  });

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  simulateOpen() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  simulateMessage(data: unknown) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(data) }));
  }

  simulateError() {
    this.onerror?.(new Event("error"));
  }

  simulateClose() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.(new CloseEvent("close"));
  }
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------
beforeEach(() => {
  vi.useFakeTimers();
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("useWebSocket", () => {
  it("connects on mount and subscribes to channels", () => {
    renderHook(() =>
      useWebSocket({ url: "ws://localhost/ws", channels: ["state"] }),
    );

    expect(MockWebSocket.instances).toHaveLength(1);
    const ws = MockWebSocket.instances[0]!;
    expect(ws.url).toBe("ws://localhost/ws");

    act(() => ws.simulateOpen());

    expect(ws.send).toHaveBeenCalledWith(
      JSON.stringify({ channel: "subscribe", data: ["state"] }),
    );
  });

  it("sets connected to true on open, false on close", () => {
    const { result } = renderHook(() =>
      useWebSocket({ url: "ws://localhost/ws" }),
    );

    expect(result.current.connected).toBe(false);

    const ws = MockWebSocket.instances[0]!;
    act(() => ws.simulateOpen());
    expect(result.current.connected).toBe(true);

    act(() => ws.simulateClose());
    expect(result.current.connected).toBe(false);
  });

  it("dispatches parsed messages to onMessage callback", () => {
    const onMessage = vi.fn();
    renderHook(() =>
      useWebSocket({ url: "ws://localhost/ws", onMessage }),
    );

    const ws = MockWebSocket.instances[0]!;
    act(() => ws.simulateOpen());

    const payload = { channel: "state", data: { foo: 1 } };
    act(() => ws.simulateMessage(payload));

    expect(onMessage).toHaveBeenCalledWith(payload);
  });

  it("ignores malformed JSON messages", () => {
    const onMessage = vi.fn();
    renderHook(() =>
      useWebSocket({ url: "ws://localhost/ws", onMessage }),
    );

    const ws = MockWebSocket.instances[0]!;
    act(() => ws.simulateOpen());

    act(() => {
      ws.onmessage?.(new MessageEvent("message", { data: "not-json{" }));
    });

    expect(onMessage).not.toHaveBeenCalled();
  });

  it("reconnects with exponential backoff on close", () => {
    renderHook(() =>
      useWebSocket({
        url: "ws://localhost/ws",
        reconnectMs: 1000,
        maxReconnectMs: 16000,
      }),
    );

    expect(MockWebSocket.instances).toHaveLength(1);
    const ws1 = MockWebSocket.instances[0]!;

    // Simulate close — should schedule reconnect after ~1000ms (attempt 0)
    act(() => ws1.simulateClose());

    // Advance less than base delay — no reconnect yet
    act(() => vi.advanceTimersByTime(800));
    expect(MockWebSocket.instances).toHaveLength(1);

    // Advance past first backoff (1000ms + up to 200ms jitter)
    act(() => vi.advanceTimersByTime(400));
    expect(MockWebSocket.instances).toHaveLength(2);

    // Second close — should reconnect after ~2000ms (attempt 1)
    const ws2 = MockWebSocket.instances[1]!;
    act(() => ws2.simulateClose());

    act(() => vi.advanceTimersByTime(1800));
    expect(MockWebSocket.instances).toHaveLength(2);

    act(() => vi.advanceTimersByTime(700));
    expect(MockWebSocket.instances).toHaveLength(3);
  });

  it("resets backoff counter after successful connection", () => {
    renderHook(() =>
      useWebSocket({ url: "ws://localhost/ws", reconnectMs: 1000 }),
    );

    const ws1 = MockWebSocket.instances[0]!;
    act(() => ws1.simulateClose());
    act(() => vi.advanceTimersByTime(1500));
    expect(MockWebSocket.instances).toHaveLength(2);

    // Open successfully, then close again — delay should reset to base
    const ws2 = MockWebSocket.instances[1]!;
    act(() => ws2.simulateOpen());
    act(() => ws2.simulateClose());

    // Should reconnect after ~1000ms (reset), not ~2000ms
    act(() => vi.advanceTimersByTime(1500));
    expect(MockWebSocket.instances).toHaveLength(3);
  });

  it("caps backoff at maxReconnectMs", () => {
    renderHook(() =>
      useWebSocket({
        url: "ws://localhost/ws",
        reconnectMs: 1000,
        maxReconnectMs: 4000,
      }),
    );

    // Close 4 times to push backoff: 1s, 2s, 4s, 4s (capped)
    for (let i = 0; i < 4; i++) {
      const ws = MockWebSocket.instances[MockWebSocket.instances.length - 1]!;
      act(() => ws.simulateClose());
      // Advance past the max + jitter
      act(() => vi.advanceTimersByTime(5000));
    }

    // All reconnections should have happened
    expect(MockWebSocket.instances.length).toBe(5);
  });

  it("does not reconnect after unmount", () => {
    const { unmount } = renderHook(() =>
      useWebSocket({ url: "ws://localhost/ws", reconnectMs: 500 }),
    );

    expect(MockWebSocket.instances).toHaveLength(1);

    unmount();

    // The close() called by cleanup may trigger onclose, which should not
    // schedule a reconnect because mountedRef is false.
    act(() => vi.advanceTimersByTime(10000));
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("does not open duplicate connections when already CONNECTING", () => {
    const { rerender } = renderHook(() =>
      useWebSocket({ url: "ws://localhost/ws" }),
    );

    expect(MockWebSocket.instances).toHaveLength(1);
    // Socket is still in CONNECTING state. Re-render should not create another.
    rerender();
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("does not create a new connection when already OPEN", () => {
    const { rerender } = renderHook(() =>
      useWebSocket({ url: "ws://localhost/ws" }),
    );

    const ws = MockWebSocket.instances[0]!;
    act(() => ws.simulateOpen());

    rerender();
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("stable channels array does not cause reconnect", () => {
    // Each render passes a new array literal with the same contents.
    // The hook should stabilise it via useMemo to prevent effect re-runs.
    const { rerender } = renderHook(
      ({ channels }: { channels: string[] }) =>
        useWebSocket({ url: "ws://localhost/ws", channels }),
      { initialProps: { channels: ["state", "tower"] } },
    );

    const ws = MockWebSocket.instances[0]!;
    act(() => ws.simulateOpen());
    expect(MockWebSocket.instances).toHaveLength(1);

    // Re-render with a new array reference but same contents
    rerender({ channels: ["state", "tower"] });
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(ws.close).not.toHaveBeenCalled();
  });

  it("send() transmits JSON when connected", () => {
    const { result } = renderHook(() =>
      useWebSocket({ url: "ws://localhost/ws" }),
    );

    const ws = MockWebSocket.instances[0]!;
    act(() => ws.simulateOpen());

    act(() => result.current.send({ action: "ping" }));
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ action: "ping" }));
  });

  it("send() is a no-op when not connected", () => {
    const { result } = renderHook(() =>
      useWebSocket({ url: "ws://localhost/ws" }),
    );

    const ws = MockWebSocket.instances[0]!;
    // Socket is still CONNECTING — send should not transmit
    const callsBefore = ws.send.mock.calls.length;
    act(() => result.current.send({ action: "ping" }));
    expect(ws.send.mock.calls.length).toBe(callsBefore);
  });

  it("closes socket on error then reconnects", () => {
    renderHook(() =>
      useWebSocket({ url: "ws://localhost/ws", reconnectMs: 500 }),
    );

    const ws = MockWebSocket.instances[0]!;
    act(() => ws.simulateOpen());
    act(() => ws.simulateError());

    // Error triggers close(), which triggers onclose, which schedules reconnect
    act(() => vi.advanceTimersByTime(700));
    expect(MockWebSocket.instances).toHaveLength(2);
  });
});

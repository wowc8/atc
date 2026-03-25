import { useEffect, useRef, useCallback, useState, useMemo } from "react";

export type WsMessage =
  | { channel: "state"; data: unknown }
  | { channel: string; data: unknown };

interface UseWebSocketOptions {
  url?: string;
  channels?: string[];
  onMessage?: (msg: WsMessage) => void;
  reconnectMs?: number;
  maxReconnectMs?: number;
}

const MAX_RECONNECT_MS_DEFAULT = 30_000;

export function useWebSocket({
  url = (typeof window !== "undefined" && "__TAURI_INTERNALS__" in window) ? "ws://127.0.0.1:8420/ws" : `ws://${window.location.host}/ws`,
  channels = ["state"],
  onMessage,
  reconnectMs = 1_000,
  maxReconnectMs = MAX_RECONNECT_MS_DEFAULT,
}: UseWebSocketOptions = {}) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const attemptRef = useRef(0);
  const mountedRef = useRef(true);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  // Stabilise the channels array so a new literal each render doesn't
  // invalidate the connect callback and trigger a reconnect storm.
  const stableChannels = useMemo(
    () => channels,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [channels.join(",")],
  );

  const connect = useCallback(() => {
    if (!mountedRef.current) return;

    const existing = wsRef.current;
    if (
      existing &&
      (existing.readyState === WebSocket.OPEN ||
        existing.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) {
        ws.close();
        return;
      }
      attemptRef.current = 0;
      setConnected(true);
      ws.send(JSON.stringify({ channel: "subscribe", data: stableChannels }));
    };

    ws.onmessage = (evt) => {
      if (typeof evt.data === "string") {
        try {
          const msg = JSON.parse(evt.data) as WsMessage;
          onMessageRef.current?.(msg);
        } catch {
          /* ignore malformed frames */
        }
      }
    };

    ws.onclose = () => {
      setConnected(false);
      if (!mountedRef.current) return;
      // Exponential backoff with jitter
      const delay = Math.min(
        reconnectMs * Math.pow(2, attemptRef.current),
        maxReconnectMs,
      );
      const jitter = delay * 0.2 * Math.random();
      attemptRef.current += 1;
      reconnectTimer.current = setTimeout(connect, delay + jitter);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [url, stableChannels, reconnectMs, maxReconnectMs]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const send = useCallback((data: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  return { connected, send, ws: wsRef };
}

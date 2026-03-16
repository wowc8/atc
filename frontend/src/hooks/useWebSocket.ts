import { useEffect, useRef, useCallback, useState } from "react";

export type WsMessage =
  | { channel: "state"; data: unknown }
  | { channel: string; data: unknown };

interface UseWebSocketOptions {
  url?: string;
  channels?: string[];
  onMessage?: (msg: WsMessage) => void;
  reconnectMs?: number;
}

export function useWebSocket({
  url = `ws://${window.location.host}/ws`,
  channels = ["state"],
  onMessage,
  reconnectMs = 3000,
}: UseWebSocketOptions = {}) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      ws.send(
        JSON.stringify({ channel: "subscribe", data: channels }),
      );
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
      reconnectTimer.current = setTimeout(connect, reconnectMs);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [url, channels, reconnectMs]);

  useEffect(() => {
    connect();
    return () => {
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

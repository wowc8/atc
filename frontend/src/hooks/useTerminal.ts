import { useEffect, useRef, useCallback } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

interface UseTerminalOptions {
  /** WebSocket channel to subscribe to for terminal data (e.g. "terminal:abc-123") */
  channel?: string;
  /** Whether the terminal should be active */
  enabled?: boolean;
}

/**
 * Transform separator lines (rows of box-drawing ─ characters) into subtle,
 * dimmed dividers so they don't dominate the terminal UI.
 *
 * Claude Code emits ─ (U+2500) repeated across the full terminal width as
 * section separators.  In xterm.js these render as thick grey bars.  We
 * wrap them with ANSI dim + dark-grey so they fade into the background.
 */
function transformSeparators(data: string): string {
  // Match runs of 3+ box-drawing horizontal chars (─ ━ ╌ ╍ ┄ ┅ ┈ ┉)
  // that make up separator lines, possibly surrounded by ANSI escapes.
  // The regex targets sequences that appear as full-width separator rows.
  return data.replace(
    /([─━╌╍┄┅┈┉]{3,})/g,
    "\x1b[2m\x1b[38;5;238m$1\x1b[0m",
  );
}

/**
 * Hook that manages an xterm.js Terminal instance and connects it to the
 * ATC WebSocket for streaming PTY output.
 *
 * Returns a ref callback to attach to a container div.
 */
export function useTerminal({ channel, enabled = true }: UseTerminalOptions) {
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Create terminal once
  useEffect(() => {
    if (!enabled) return;

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: '"SF Mono", "Fira Code", "Fira Mono", "Roboto Mono", monospace',
      theme: {
        background: "#0d1117",
        foreground: "#e6edf3",
        cursor: "#58a6ff",
        selectionBackground: "#388bfd33",
        black: "#0d1117",
        red: "#f85149",
        green: "#3fb950",
        yellow: "#d29922",
        blue: "#58a6ff",
        magenta: "#bc8cff",
        cyan: "#39d353",
        white: "#e6edf3",
      },
      scrollback: 5000,
      convertEol: true,
    });

    const fit = new FitAddon();
    term.loadAddon(fit);

    termRef.current = term;
    fitRef.current = fit;

    // If container already mounted, open immediately
    if (containerRef.current) {
      term.open(containerRef.current);
      fit.fit();
    }

    return () => {
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, [enabled]);

  // Fit on resize
  useEffect(() => {
    if (!enabled) return;
    const handleResize = () => fitRef.current?.fit();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [enabled]);

  // WebSocket connection for terminal channel.
  // The `cancelled` flag gates every callback so that React StrictMode
  // double-mount cannot cause two live subscriptions.  Without these guards
  // the first WebSocket's `onopen` can fire (and subscribe) before the
  // cleanup's `ws.close()` takes effect, leading to duplicate lines.
  useEffect(() => {
    if (!enabled || !channel) return;

    let cancelled = false;

    function connect() {
      if (cancelled) return;
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
      wsRef.current = ws;

      ws.onopen = () => {
        if (cancelled) {
          ws.close();
          return;
        }
        ws.send(JSON.stringify({ channel: "subscribe", data: [channel] }));
      };

      ws.onmessage = (evt) => {
        if (cancelled) return;
        try {
          const msg = JSON.parse(evt.data);
          if (msg.channel === channel && msg.data) {
            const raw =
              typeof msg.data === "string" ? msg.data : JSON.stringify(msg.data);
            termRef.current?.write(transformSeparators(raw));
          }
        } catch {
          /* ignore malformed */
        }
      };

      ws.onclose = () => {
        if (!cancelled) {
          reconnectRef.current = setTimeout(connect, 3000);
        }
      };

      ws.onerror = () => ws.close();
    }

    connect();

    return () => {
      cancelled = true;
      clearTimeout(reconnectRef.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [channel, enabled]);

  // Handle user input → send to backend via WebSocket
  useEffect(() => {
    if (!enabled || !channel) return;
    const term = termRef.current;
    if (!term) return;

    const disposable = term.onData((data) => {
      const ws = wsRef.current;
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ channel, data }));
      }
    });

    return () => disposable.dispose();
  }, [channel, enabled]);

  // Ref callback for container div — handles initial open and re-attach
  // after the container is re-mounted (e.g. minimize → restore).
  const attachRef = useCallback((el: HTMLDivElement | null) => {
    containerRef.current = el;
    if (el && termRef.current) {
      const term = termRef.current;
      // If the terminal's element is missing or is a detached DOM node,
      // re-open on the new container.
      if (!term.element || !el.contains(term.element)) {
        term.open(el);
      }
      // Delay fit to ensure container has dimensions
      requestAnimationFrame(() => fitRef.current?.fit());
    }
  }, []);

  const writeLine = useCallback((text: string) => {
    termRef.current?.writeln(text);
  }, []);

  const fit = useCallback(() => {
    fitRef.current?.fit();
  }, []);

  /** Send text + Enter to the terminal's PTY via WebSocket. */
  const sendInput = useCallback(
    (text: string) => {
      const ws = wsRef.current;
      if (ws?.readyState === WebSocket.OPEN && channel) {
        ws.send(JSON.stringify({ channel, data: text + "\r" }));
      }
    },
    [channel],
  );

  return { attachRef, terminal: termRef, writeLine, fit, sendInput };
}

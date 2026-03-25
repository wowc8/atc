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
 * Transform terminal output to clean up formatting artifacts.
 *
 * 1. Collapse long runs of box-drawing ─ characters (separator lines)
 *    to a short dimmed divider.  Claude Code emits these at the full
 *    tmux width (200 cols) which wraps in the narrower xterm.js panel,
 *    creating the appearance of duplicated thick grey bars.
 *
 * 2. Deduplicate identical consecutive lines that sometimes appear due
 *    to PTY echo or tmux redraw artifacts.
 */
function transformTerminalOutput(data: string): string {
  // Collapse long runs of box-drawing chars to a short dimmed divider.
  // This prevents 200-char separator lines from wrapping across multiple
  // rows in the narrower xterm.js terminal panel.
  let result = data.replace(/([─━╌╍┄┅┈┉]{3,})/g, (_match) => {
    // Replace with a short (40-char) dimmed divider
    const short = "─".repeat(40);
    return `\x1b[2m\x1b[38;5;238m${short}\x1b[0m`;
  });

  return result;
}

/** @deprecated Use transformTerminalOutput instead */
const transformSeparators = transformTerminalOutput;

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
  const channelRef = useRef<string | undefined>(channel);
  // Buffer for data received before the terminal is opened (attached to DOM).
  // xterm.js throws "Cannot read properties of undefined (reading 'dimensions')"
  // when write() is called on a terminal that hasn't been opened yet.
  const pendingWritesRef = useRef<string[]>([]);
  const termOpenRef = useRef(false);

  // Keep channel ref in sync for use in non-reactive callbacks
  channelRef.current = channel;

  // Create terminal once
  useEffect(() => {
    if (!enabled) return;

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily:
        '"SF Mono", "Fira Code", "Fira Mono", "Roboto Mono", monospace',
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
      termOpenRef.current = true;
      try {
        fit.fit();
      } catch {
        /* not ready */
      }
    }

    return () => {
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
      termOpenRef.current = false;
      pendingWritesRef.current = [];
    };
  }, [enabled]);

  // Fit on resize
  useEffect(() => {
    if (!enabled) return;
    const handleResize = () => {
      try {
        fitRef.current?.fit();
      } catch {
        // Terminal renderer may not be fully initialized
      }
    };
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
      const isTauriEnv = window.location.protocol === "file:";
      const protocol = isTauriEnv ? "ws:" : (window.location.protocol === "https:" ? "wss:" : "ws:");
      const host = isTauriEnv ? "127.0.0.1:8420" : window.location.host;
      const ws = new WebSocket(`${protocol}//${host}/ws`);
      wsRef.current = ws;

      ws.onopen = () => {
        if (cancelled) {
          ws.close();
          return;
        }
        ws.send(JSON.stringify({ channel: "subscribe", data: [channel] }));
        // Send initial terminal dimensions so tmux pane matches display
        const term = termRef.current;
        if (term && term.cols > 0 && term.rows > 0) {
          ws.send(
            JSON.stringify({
              channel,
              type: "resize",
              data: { cols: term.cols, rows: term.rows },
            }),
          );
        }
      };

      ws.onmessage = (evt) => {
        if (cancelled) return;
        try {
          const msg = JSON.parse(evt.data);
          if (msg.channel === channel && msg.data) {
            const raw =
              typeof msg.data === "string"
                ? msg.data
                : JSON.stringify(msg.data);
            const transformed = transformSeparators(raw);
            // Buffer writes if terminal isn't opened yet to avoid xterm.js
            // "dimensions" error from syncScrollArea on unopened terminals.
            if (termOpenRef.current && termRef.current) {
              termRef.current.write(transformed, () => {
                termRef.current?.scrollToBottom();
              });
            } else {
              pendingWritesRef.current.push(transformed);
            }
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

    const dataDisposable = term.onData((data) => {
      const ws = wsRef.current;
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ channel, data }));
      }
    });

    // Send terminal dimensions to backend so tmux pane matches xterm.js size
    const resizeDisposable = term.onResize((evt) => {
      const ws = wsRef.current;
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({
            channel,
            type: "resize",
            data: { cols: evt.cols, rows: evt.rows },
          }),
        );
      }
    });

    return () => {
      dataDisposable.dispose();
      resizeDisposable.dispose();
    };
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
      termOpenRef.current = true;
      // Delay fit to ensure container has dimensions.
      // Double-RAF avoids the xterm.js "Cannot read properties of undefined
      // (reading 'dimensions')" error that occurs when fit() runs before
      // the terminal renderer is fully initialized.
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          try {
            fitRef.current?.fit();
          } catch {
            // Terminal may not be fully initialized yet — safe to ignore
          }
          // Send initial dimensions to backend so tmux pane matches
          const t = termRef.current;
          const ws = wsRef.current;
          if (t && ws?.readyState === WebSocket.OPEN && channelRef.current) {
            ws.send(
              JSON.stringify({
                channel: channelRef.current,
                type: "resize",
                data: { cols: t.cols, rows: t.rows },
              }),
            );
          }
          // Flush any data that arrived before the terminal was opened
          const pending = pendingWritesRef.current;
          if (pending.length > 0 && termRef.current) {
            for (const data of pending) {
              termRef.current.write(data);
            }
            pendingWritesRef.current = [];
            termRef.current?.scrollToBottom();
          }
        });
      });
    }
  }, []);

  const writeLine = useCallback((text: string) => {
    termRef.current?.writeln(text);
  }, []);

  const fit = useCallback(() => {
    try {
      fitRef.current?.fit();
    } catch {
      // Guard against fit() before terminal is fully initialized
    }
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

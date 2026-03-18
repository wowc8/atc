import { Component, type ErrorInfo, type ReactNode } from "react";
import { captureException, sendReport } from "../../utils/sentry";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  reportSent: boolean;
  reportSending: boolean;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = {
    hasError: false,
    error: null,
    reportSent: false,
    reportSending: false,
  };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    captureException(error, { componentStack: info.componentStack ?? "" });
  }

  handleSendReport = async () => {
    const { error } = this.state;
    if (!error) return;
    this.setState({ reportSending: true });
    await sendReport(`[Frontend Crash] ${error.message}`, {
      stack: error.stack ?? "",
      source: "error_boundary",
    });
    this.setState({ reportSent: true, reportSending: false });
  };

  handleReload = () => {
    window.location.reload();
  };

  render() {
    if (!this.state.hasError) return this.props.children;

    if (this.props.fallback) return this.props.fallback;

    return (
      <div style={styles.container}>
        <div style={styles.card}>
          <h2 style={styles.title}>Something went wrong</h2>
          <p style={styles.message}>
            An unexpected error occurred. You can send a crash report to help us
            fix this issue.
          </p>
          {this.state.error && (
            <pre style={styles.errorDetail}>{this.state.error.message}</pre>
          )}
          <div style={styles.actions}>
            <button
              style={styles.reportBtn}
              onClick={this.handleSendReport}
              disabled={this.state.reportSent || this.state.reportSending}
            >
              {this.state.reportSent
                ? "Report Sent"
                : this.state.reportSending
                  ? "Sending..."
                  : "Send Report"}
            </button>
            <button style={styles.reloadBtn} onClick={this.handleReload}>
              Reload Page
            </button>
          </div>
        </div>
      </div>
    );
  }
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    minHeight: "100vh",
    padding: "2rem",
    background: "var(--color-bg, #111)",
  },
  card: {
    maxWidth: 480,
    width: "100%",
    padding: "2rem",
    borderRadius: 8,
    background: "var(--color-bg-raised, #1a1a1a)",
    border: "1px solid var(--color-border-subtle, #333)",
  },
  title: {
    fontSize: "1.25rem",
    fontWeight: 600,
    marginBottom: "0.75rem",
    color: "var(--color-text, #eee)",
  },
  message: {
    fontSize: "0.875rem",
    color: "var(--color-text-secondary, #999)",
    marginBottom: "1rem",
  },
  errorDetail: {
    fontSize: "0.75rem",
    color: "var(--color-status-red, #ef4444)",
    background: "var(--color-bg, #111)",
    padding: "0.75rem",
    borderRadius: 4,
    overflow: "auto",
    maxHeight: 120,
    marginBottom: "1rem",
  },
  actions: {
    display: "flex",
    gap: "0.5rem",
  },
  reportBtn: {
    padding: "0.5rem 1rem",
    fontSize: "0.875rem",
    borderRadius: 4,
    border: "1px solid var(--color-accent, #3b82f6)",
    background: "var(--color-accent, #3b82f6)",
    color: "#fff",
    cursor: "pointer",
  },
  reloadBtn: {
    padding: "0.5rem 1rem",
    fontSize: "0.875rem",
    borderRadius: 4,
    border: "1px solid var(--color-border-subtle, #333)",
    background: "transparent",
    color: "var(--color-text, #eee)",
    cursor: "pointer",
  },
};

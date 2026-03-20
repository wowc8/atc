import { useTerminal } from "../../hooks/useTerminal";
import type { Session } from "../../types";
import "./AceTerminal.css";

interface AceTerminalProps {
  session: Session;
}

export default function AceTerminal({ session }: AceTerminalProps) {
  const isActive =
    session.status === "working" ||
    session.status === "waiting" ||
    session.status === "idle" ||
    session.status === "connecting";

  // Keep terminal enabled even for error state so past output remains visible
  const showTerminal = isActive || session.status === "error";

  const { attachRef } = useTerminal({
    channel: `terminal:${session.id}`,
    enabled: showTerminal,
  });

  return (
    <div className="ace-terminal" data-testid="ace-terminal">
      <div className="ace-terminal__view" ref={attachRef} />
    </div>
  );
}

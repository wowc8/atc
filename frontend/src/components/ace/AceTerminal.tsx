import { useState } from "react";
import { api } from "../../utils/api";
import { useTerminal } from "../../hooks/useTerminal";
import type { Session } from "../../types";
import "./AceTerminal.css";

interface AceTerminalProps {
  session: Session;
}

export default function AceTerminal({ session }: AceTerminalProps) {
  const [message, setMessage] = useState("");

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

  async function handleSendMessage(e: React.FormEvent) {
    e.preventDefault();
    if (!message.trim()) return;
    try {
      await api.post(`/aces/${session.id}/message`, {
        message: message.trim(),
      });
      setMessage("");
    } catch (err) {
      console.error("Failed to send message:", err);
    }
  }

  return (
    <div className="ace-terminal" data-testid="ace-terminal">
      <div className="ace-terminal__view" ref={attachRef} />

      <form className="ace-terminal__input" onSubmit={handleSendMessage}>
        <input
          type="text"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder={`Send to ${session.name}...`}
          disabled={!isActive}
        />
        <button
          type="submit"
          className="btn btn-sm btn-primary"
          disabled={!message.trim() || !isActive}
        >
          Send
        </button>
      </form>
    </div>
  );
}

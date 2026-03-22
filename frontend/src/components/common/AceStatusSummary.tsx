import type { Session } from "../../types";

interface Props {
  sessions: Session[];
  projectId: string;
}

export default function AceStatusSummary({ sessions, projectId }: Props) {
  const aces = sessions.filter(
    (s) => s.project_id === projectId && s.session_type === "ace",
  );

  if (aces.length === 0) {
    return <span className="ace-status ace-status--none">0 active</span>;
  }

  const working = aces.filter((s) => s.status === "working").length;
  const waiting = aces.filter((s) => s.status === "waiting").length;
  const idle = aces.filter(
    (s) => s.status === "idle" || s.status === "paused" || s.status === "disconnected",
  ).length;

  if (working === 0 && waiting === 0) {
    return <span className="ace-status ace-status--none">0 active</span>;
  }

  const parts: React.ReactNode[] = [];

  if (working > 0) {
    parts.push(
      <span key="working" className="ace-status__item">
        <span className="ace-status__dot ace-status__dot--working" />
        {working} working
      </span>,
    );
  }
  if (waiting > 0) {
    parts.push(
      <span key="waiting" className="ace-status__item">
        <span className="ace-status__dot ace-status__dot--waiting" />
        {waiting} waiting
      </span>,
    );
  }
  if (idle > 0) {
    parts.push(
      <span key="idle" className="ace-status__item">
        <span className="ace-status__dot ace-status__dot--idle" />
        {idle} idle
      </span>,
    );
  }

  return (
    <span className="ace-status">
      {parts.map((part, i) => (
        <span key={i}>
          {i > 0 && <span className="ace-status__sep"> · </span>}
          {part}
        </span>
      ))}
    </span>
  );
}

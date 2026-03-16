import type { Leader } from "../../types";
import "./ContextViewer.css";

interface ContextViewerProps {
  leader: Leader | undefined;
}

export default function ContextViewer({ leader }: ContextViewerProps) {
  if (!leader?.context || Object.keys(leader.context).length === 0) {
    return (
      <div className="context-viewer" data-testid="context-viewer">
        <h3>Context</h3>
        <p className="context-viewer__empty">No context entries yet.</p>
      </div>
    );
  }

  const entries = Object.entries(leader.context);

  return (
    <div className="context-viewer" data-testid="context-viewer">
      <h3>Context</h3>
      <div className="context-viewer__entries">
        {entries.map(([key, value]) => (
          <div key={key} className="context-viewer__entry">
            <span className="context-viewer__key">{key}</span>
            <span className="context-viewer__value">
              {typeof value === "string"
                ? value
                : JSON.stringify(value, null, 2)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

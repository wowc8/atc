interface Props {
  done: number;
  inProgress: number;
  todo: number;
  width?: number;
}

export default function KanbanBar({ done, inProgress, todo, width }: Props) {
  const total = done + inProgress + todo;
  if (total === 0) {
    return (
      <div
        className="kanban-bar kanban-bar--empty"
        style={width !== undefined ? { width } : undefined}
        title="No tasks"
      />
    );
  }

  const donePct = (done / total) * 100;
  const inProgressPct = (inProgress / total) * 100;
  const todoPct = (todo / total) * 100;
  const tooltipText = `${done} done / ${inProgress} in progress / ${todo} todo`;

  return (
    <div
      className="kanban-bar"
      style={width !== undefined ? { width } : undefined}
      title={tooltipText}
      aria-label={tooltipText}
    >
      {done > 0 && (
        <div
          className="kanban-bar__segment kanban-bar__segment--done"
          style={{ width: `${donePct}%` }}
        />
      )}
      {inProgress > 0 && (
        <div
          className="kanban-bar__segment kanban-bar__segment--in-progress"
          style={{ width: `${inProgressPct}%` }}
        />
      )}
      {todo > 0 && (
        <div
          className="kanban-bar__segment kanban-bar__segment--todo"
          style={{ width: `${todoPct}%` }}
        />
      )}
    </div>
  );
}

import StatusBadge from "../common/StatusBadge";
import type { Task } from "../../types";
import "./TaskBoard.css";

interface TaskBoardProps {
  tasks: Task[];
}

const COLUMNS = ["pending", "in_progress", "done"] as const;

export default function TaskBoard({ tasks }: TaskBoardProps) {
  return (
    <div className="task-board" data-testid="task-board">
      <h3>Tasks</h3>
      {tasks.length === 0 ? (
        <p className="task-board__empty">No tasks yet.</p>
      ) : (
        <div className="task-board__grid">
          {COLUMNS.map((col) => {
            const colTasks = tasks.filter((t) => t.status === col);
            return (
              <div key={col} className="task-board__col">
                <h4>
                  {col.replace(/_/g, " ")}
                  <span className="task-board__col-count">
                    {colTasks.length}
                  </span>
                </h4>
                {colTasks.map((task) => (
                  <div key={task.id} className="task-board__card">
                    <div className="task-board__card-title">{task.title}</div>
                    <div className="task-board__card-meta">
                      <StatusBadge status={task.status} size="sm" />
                      {task.assigned_to && (
                        <span className="task-board__assignee">
                          {task.assigned_to.slice(0, 8)}
                        </span>
                      )}
                    </div>
                    {task.description && (
                      <p className="task-board__card-desc">
                        {task.description.length > 80
                          ? task.description.slice(0, 80) + "..."
                          : task.description}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

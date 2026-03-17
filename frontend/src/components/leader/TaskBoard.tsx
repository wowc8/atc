import { useState, useCallback } from "react";
import StatusBadge from "../common/StatusBadge";
import type { TaskGraph } from "../../types";
import { api } from "../../utils/api";
import "./TaskBoard.css";

interface TaskBoardProps {
  projectId: string;
  taskGraphs: TaskGraph[];
  onRefresh: () => Promise<void>;
}

type ViewMode = "kanban" | "table";

const COLUMNS = ["todo", "in_progress", "done"] as const;

const COLUMN_LABELS: Record<string, string> = {
  todo: "Todo",
  in_progress: "In Progress",
  done: "Done",
};

export default function TaskBoard({
  projectId,
  taskGraphs,
  onRefresh,
}: TaskBoardProps) {
  const [viewMode, setViewMode] = useState<ViewMode>("kanban");
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState("");

  const handleCreate = useCallback(async () => {
    if (!newTitle.trim()) return;
    await api.post(`/projects/${projectId}/task-graphs`, {
      title: newTitle.trim(),
    });
    setNewTitle("");
    setCreating(false);
    await onRefresh();
  }, [projectId, newTitle, onRefresh]);

  const handleStatusChange = useCallback(
    async (taskGraphId: string, newStatus: string) => {
      await api.patch(`/task-graphs/${taskGraphId}/status`, {
        status: newStatus,
      });
      await onRefresh();
    },
    [onRefresh],
  );

  const handleDelete = useCallback(
    async (taskGraphId: string) => {
      await api.delete(`/task-graphs/${taskGraphId}`);
      await onRefresh();
    },
    [onRefresh],
  );

  return (
    <div className="task-board" data-testid="task-board">
      <div className="task-board__header">
        <h3>Tasks</h3>
        <div className="task-board__controls">
          <div
            className="task-board__view-toggle"
            data-testid="view-toggle"
          >
            <button
              className={`btn btn-sm ${viewMode === "kanban" ? "btn-primary" : ""}`}
              onClick={() => setViewMode("kanban")}
              data-testid="view-toggle-kanban"
            >
              Kanban
            </button>
            <button
              className={`btn btn-sm ${viewMode === "table" ? "btn-primary" : ""}`}
              onClick={() => setViewMode("table")}
              data-testid="view-toggle-table"
            >
              Table
            </button>
          </div>
          <button
            className="btn btn-sm btn-primary"
            onClick={() => setCreating(true)}
            data-testid="add-task-btn"
          >
            + Add
          </button>
        </div>
      </div>

      {creating && (
        <div className="task-board__create-form" data-testid="create-form">
          <input
            type="text"
            className="task-board__create-input"
            placeholder="Task title..."
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleCreate();
              if (e.key === "Escape") setCreating(false);
            }}
            data-testid="create-input"
            autoFocus
          />
          <div className="task-board__create-actions">
            <button
              className="btn btn-sm btn-primary"
              onClick={() => void handleCreate()}
              data-testid="create-submit"
            >
              Create
            </button>
            <button
              className="btn btn-sm"
              onClick={() => {
                setCreating(false);
                setNewTitle("");
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {taskGraphs.length === 0 && !creating ? (
        <p className="task-board__empty">No tasks yet.</p>
      ) : viewMode === "kanban" ? (
        <KanbanView
          taskGraphs={taskGraphs}
          onStatusChange={handleStatusChange}
          onDelete={handleDelete}
        />
      ) : (
        <TableView
          taskGraphs={taskGraphs}
          onStatusChange={handleStatusChange}
          onDelete={handleDelete}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Kanban View
// ---------------------------------------------------------------------------

interface ViewProps {
  taskGraphs: TaskGraph[];
  onStatusChange: (id: string, status: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

function KanbanView({ taskGraphs, onStatusChange, onDelete }: ViewProps) {
  return (
    <div className="task-board__grid" data-testid="kanban-view">
      {COLUMNS.map((col) => {
        const colTasks = taskGraphs.filter((t) => t.status === col);
        return (
          <div key={col} className="task-board__col">
            <h4>
              {COLUMN_LABELS[col]}
              <span className="task-board__col-count">{colTasks.length}</span>
            </h4>
            {colTasks.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                onStatusChange={onStatusChange}
                onDelete={onDelete}
              />
            ))}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Table View
// ---------------------------------------------------------------------------

function TableView({ taskGraphs, onStatusChange, onDelete }: ViewProps) {
  return (
    <div className="task-board__table-wrap" data-testid="table-view">
      <table className="task-board__table">
        <thead>
          <tr>
            <th>Title</th>
            <th>Status</th>
            <th>Assignee</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {taskGraphs.map((task) => (
            <tr key={task.id}>
              <td>
                <span className="task-board__table-title">{task.title}</span>
                {task.description && (
                  <span className="task-board__table-desc">
                    {task.description}
                  </span>
                )}
              </td>
              <td>
                <StatusBadge status={task.status} size="sm" />
              </td>
              <td>
                {task.assigned_ace_id ? (
                  <span className="task-board__assignee">
                    {task.assigned_ace_id.slice(0, 8)}
                  </span>
                ) : (
                  <span className="task-board__unassigned">—</span>
                )}
              </td>
              <td>
                <div className="task-board__table-actions">
                  {task.status !== "in_progress" && (
                    <button
                      className="btn btn-sm"
                      onClick={() => void onStatusChange(task.id, "in_progress")}
                    >
                      Start
                    </button>
                  )}
                  {task.status !== "done" && (
                    <button
                      className="btn btn-sm"
                      onClick={() => void onStatusChange(task.id, "done")}
                    >
                      Done
                    </button>
                  )}
                  {task.status === "done" && (
                    <button
                      className="btn btn-sm"
                      onClick={() => void onStatusChange(task.id, "todo")}
                    >
                      Reopen
                    </button>
                  )}
                  <button
                    className="btn btn-sm btn-danger"
                    onClick={() => void onDelete(task.id)}
                  >
                    Del
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Task Card (Kanban)
// ---------------------------------------------------------------------------

interface TaskCardProps {
  task: TaskGraph;
  onStatusChange: (id: string, status: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

function TaskCard({ task, onStatusChange, onDelete }: TaskCardProps) {
  return (
    <div className="task-board__card" data-testid="task-card">
      <div className="task-board__card-title">{task.title}</div>
      <div className="task-board__card-meta">
        <StatusBadge status={task.status} size="sm" />
        {task.assigned_ace_id && (
          <span className="task-board__assignee">
            {task.assigned_ace_id.slice(0, 8)}
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
      <div className="task-board__card-actions">
        {task.status === "todo" && (
          <button
            className="btn btn-sm"
            onClick={() => void onStatusChange(task.id, "in_progress")}
          >
            Start
          </button>
        )}
        {task.status === "in_progress" && (
          <button
            className="btn btn-sm"
            onClick={() => void onStatusChange(task.id, "done")}
          >
            Done
          </button>
        )}
        {task.status === "done" && (
          <button
            className="btn btn-sm"
            onClick={() => void onStatusChange(task.id, "todo")}
          >
            Reopen
          </button>
        )}
        <button
          className="btn btn-sm btn-danger"
          onClick={() => void onDelete(task.id)}
        >
          Del
        </button>
      </div>
    </div>
  );
}

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  useDroppable,
} from "@dnd-kit/core";
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import type { Project, Session, Task, Leader, GitHubSummary } from "../../types";
import { getProjectMilestoneStatus } from "../../utils/milestones";
import AceStatusSummary from "../common/AceStatusSummary";
import { api, ApiError } from "../../utils/api";

type ColumnStatus = "active" | "paused" | "archived";

interface Props {
  projects: Project[];
  sessions: Session[];
  tasks: Record<string, Task[]>;
  leaders: Record<string, Leader>;
  github: Record<string, GitHubSummary>;
  onProjectsChange: (projects: Project[]) => void;
}

interface CardProps {
  project: Project;
  sessions: Session[];
  tasks: Task[];
  leader: Leader | undefined;
}

function SortableCard({ project, sessions, tasks, leader }: CardProps) {
  const navigate = useNavigate();
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: project.id });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
  };

  const ms = getProjectMilestoneStatus(tasks);
  return (
    <div
      ref={setNodeRef}
      style={style}
      className="board-card"
      {...attributes}
      {...listeners}
    >
      <div className="board-card__header">
        <span className="board-card__name" onClick={() => navigate(`/projects/${project.id}`)}>
          {project.name}
        </span>
      </div>

      {leader && (
        <div className={`board-card__leader leader-status--${leader.status}`}>
          {leader.status}
        </div>
      )}

      <div className="board-card__milestone">{ms.label}</div>

      <div className="board-card__ace-row">
        <AceStatusSummary sessions={sessions} projectId={project.id} />
      </div>
    </div>
  );
}

interface ColumnProps {
  status: ColumnStatus;
  label: string;
  projects: Project[];
  sessions: Session[];
  tasks: Record<string, Task[]>;
  leaders: Record<string, Leader>;
}

function BoardColumn({ status, label, projects, sessions, tasks, leaders }: ColumnProps) {
  const { setNodeRef, isOver } = useDroppable({ id: status });

  return (
    <div
      ref={setNodeRef}
      className={`board-column${isOver ? " board-column--over" : ""}`}
    >
      <div className="board-column__header">
        <span className="board-column__label">{label}</span>
        <span className="board-column__count">{projects.length}</span>
      </div>

      <SortableContext
        items={projects.map((p) => p.id)}
        strategy={verticalListSortingStrategy}
      >
        <div className="board-column__cards">
          {projects.length === 0 ? (
            <div className="board-column__empty">
              No {label.toLowerCase()} projects
            </div>
          ) : (
            projects.map((project) => (
              <SortableCard
                key={project.id}
                project={project}
                sessions={sessions}
                tasks={tasks[project.id] ?? []}
                leader={leaders[project.id]}
              />
            ))
          )}
        </div>
      </SortableContext>
    </div>
  );
}

const COLUMNS: { status: ColumnStatus; label: string }[] = [
  { status: "active", label: "Active" },
  { status: "paused", label: "Paused" },
  { status: "archived", label: "Archived" },
];

export default function ProjectBoardView({
  projects,
  sessions,
  tasks,
  leaders,
  onProjectsChange,
}: Props) {
  const [error, setError] = useState<string | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );

  const byStatus = (status: ColumnStatus) =>
    projects.filter((p) => p.status === status);

  const handleDragEnd = async (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over) return;

    const draggedProject = projects.find((p) => p.id === active.id);
    if (!draggedProject) return;

    // Determine target column: over.id is either a project id or a column status
    let targetStatus: ColumnStatus | null = null;
    const overProject = projects.find((p) => p.id === over.id);
    if (overProject) {
      targetStatus = overProject.status as ColumnStatus;
    } else if (["active", "paused", "archived"].includes(over.id as string)) {
      targetStatus = over.id as ColumnStatus;
    }

    if (!targetStatus) return;

    const prevProjects = [...projects];

    if (draggedProject.status !== targetStatus) {
      // Cross-column drag: update status
      const updated = projects.map((p) =>
        p.id === draggedProject.id ? { ...p, status: targetStatus! } : p,
      );
      onProjectsChange(updated);

      try {
        setError(null);
        await api.put(`/projects/${draggedProject.id}`, { status: targetStatus });
      } catch (err) {
        const msg = err instanceof ApiError ? err.message : "Status update failed";
        setError(msg);
        onProjectsChange(prevProjects);
      }
    } else if (overProject && draggedProject.id !== overProject.id) {
      // Same-column reorder
      const colProjects = byStatus(targetStatus);
      const oldIdx = colProjects.findIndex((p) => p.id === draggedProject.id);
      const newIdx = colProjects.findIndex((p) => p.id === overProject.id);
      const reorderedCol = arrayMove(colProjects, oldIdx, newIdx);

      // Merge reordered column back into full list
      const otherProjects = projects.filter((p) => p.status !== targetStatus);
      const merged = [...otherProjects, ...reorderedCol];
      onProjectsChange(merged);

      const positions = merged.map((p, i) => ({ id: p.id, position: i }));
      try {
        setError(null);
        await api.patch("/projects/reorder", { positions });
      } catch (err) {
        const msg = err instanceof ApiError ? err.message : "Reorder failed";
        setError(msg);
        onProjectsChange(prevProjects);
      }
    }
  };

  return (
    <div className="project-board-view">
      {error && <div className="view-error">{error}</div>}
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={(e) => void handleDragEnd(e)}
      >
        <div className="board-columns">
          {COLUMNS.map(({ status, label }) => (
            <BoardColumn
              key={status}
              status={status}
              label={label}
              projects={byStatus(status)}
              sessions={sessions}
              tasks={tasks}
              leaders={leaders}
            />
          ))}
        </div>
      </DndContext>
    </div>
  );
}

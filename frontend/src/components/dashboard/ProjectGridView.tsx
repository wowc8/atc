import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  useSortable,
  rectSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import type { Project, Session, Task, Leader, GitHubSummary } from "../../types";
import { getProjectMilestoneStatus } from "../../utils/milestones";
import KanbanBar from "../common/KanbanBar";
import AceStatusSummary from "../common/AceStatusSummary";
import ConfirmPopover from "../common/ConfirmPopover";
import { api, ApiError } from "../../utils/api";

interface Props {
  projects: Project[];
  sessions: Session[];
  tasks: Record<string, Task[]>;
  leaders: Record<string, Leader>;
  github: Record<string, GitHubSummary>;
  onReorder: (projects: Project[]) => void;
  onArchive: (id: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

interface CardProps {
  project: Project;
  sessions: Session[];
  tasks: Task[];
  leader: Leader | undefined;
  github: GitHubSummary | undefined;
  onArchive: (id: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

function CIBadge({ summary }: { summary: GitHubSummary | undefined }) {
  if (!summary) return <span className="ci-badge ci-badge--none">—</span>;
  const rate = summary.ci_pass_rate;
  if (rate >= 1) return <span className="ci-badge ci-badge--pass">✓</span>;
  if (rate <= 0) return <span className="ci-badge ci-badge--fail">✗</span>;
  return <span className="ci-badge ci-badge--partial">⟳</span>;
}

function StatusDot({ status }: { status: Project["status"] }) {
  return <span className={`status-dot status-dot--${status}`} />;
}

function SortableProjectCard({
  project,
  sessions,
  tasks,
  leader,
  github,
  onArchive,
  onDelete,
}: CardProps) {
  const navigate = useNavigate();
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: project.id });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
    zIndex: isDragging ? 1 : undefined,
  };

  const ms = getProjectMilestoneStatus(tasks);
  const done = tasks.filter((t) => t.status === "done").length;
  const inProgress = tasks.filter((t) => t.status === "in_progress" || t.status === "assigned").length;
  const todo = tasks.filter((t) => t.status === "pending" || t.status === "blocked").length;

  return (
    <div
      ref={setNodeRef}
      style={style}
      className="panel project-card project-card--grid"
    >
      {/* drag handle */}
      <div
        className="project-card__drag-handle"
        {...attributes}
        {...listeners}
        aria-label="Drag to reorder"
      >
        ⠿
      </div>

      <div
        className="project-card__body"
        onClick={() => navigate(`/projects/${project.id}`)}
      >
        <div className="project-card__header">
          <StatusDot status={project.status} />
          <h3 className="project-card__name">{project.name}</h3>
          <CIBadge summary={github} />
        </div>

        {leader && (
          <div className="project-card__leader">
            Leader: <span className={`leader-status leader-status--${leader.status}`}>{leader.status}</span>
          </div>
        )}

        <div className="project-card__milestone">{ms.label}</div>

        <KanbanBar done={done} inProgress={inProgress} todo={todo} />

        <div className="project-card__ace-row">
          <AceStatusSummary sessions={sessions} projectId={project.id} />
        </div>
      </div>

      <div className="project-card__actions">
        <button className="btn btn-sm" onClick={() => void onArchive(project.id)}>
          Archive
        </button>
        <ConfirmPopover
          message={`Delete "${project.name}"? This removes all tasks and context.`}
          confirmLabel="Delete"
          variant="danger"
          onConfirm={() => void onDelete(project.id)}
        >
          <button className="btn btn-sm btn-danger">Delete</button>
        </ConfirmPopover>
      </div>
    </div>
  );
}

export default function ProjectGridView({
  projects,
  sessions,
  tasks,
  leaders,
  github,
  onReorder,
  onArchive,
  onDelete,
}: Props) {
  const [reorderError, setReorderError] = useState<string | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );

  const handleDragEnd = async (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const oldIndex = projects.findIndex((p) => p.id === active.id);
    const newIndex = projects.findIndex((p) => p.id === over.id);
    const reordered = arrayMove(projects, oldIndex, newIndex);
    onReorder(reordered);

    const positions = reordered.map((p, i) => ({ id: p.id, position: i }));
    try {
      setReorderError(null);
      await api.patch("/projects/reorder", { positions });
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Reorder failed";
      setReorderError(msg);
      onReorder(projects); // revert
    }
  };

  const activeProjects = projects.filter((p) => p.status !== "archived");

  if (activeProjects.length === 0) {
    return (
      <div className="dashboard__empty">
        <p>No active projects.</p>
      </div>
    );
  }

  return (
    <>
      {reorderError && (
        <div className="view-error">{reorderError}</div>
      )}
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={(e) => void handleDragEnd(e)}
      >
        <SortableContext
          items={activeProjects.map((p) => p.id)}
          strategy={rectSortingStrategy}
        >
          <div className="project-grid-view">
            {activeProjects.map((project) => (
              <SortableProjectCard
                key={project.id}
                project={project}
                sessions={sessions}
                tasks={tasks[project.id] ?? []}
                leader={leaders[project.id]}
                github={github[project.id]}
                onArchive={onArchive}
                onDelete={onDelete}
              />
            ))}
          </div>
        </SortableContext>
      </DndContext>
    </>
  );
}

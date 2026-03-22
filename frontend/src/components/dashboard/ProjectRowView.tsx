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
  verticalListSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import type { Project, Session, Task, Leader, GitHubSummary } from "../../types";
import { getProjectMilestoneStatus } from "../../utils/milestones";
import KanbanBar from "../common/KanbanBar";
import AceStatusSummary from "../common/AceStatusSummary";
import { api, ApiError } from "../../utils/api";

interface Props {
  projects: Project[];
  sessions: Session[];
  tasks: Record<string, Task[]>;
  leaders: Record<string, Leader>;
  github: Record<string, GitHubSummary>;
  onReorder: (projects: Project[]) => void;
}

interface RowProps {
  project: Project;
  sessions: Session[];
  tasks: Task[];
  leader: Leader | undefined;
  github: GitHubSummary | undefined;
}

function CIBadgeCompact({ summary }: { summary: GitHubSummary | undefined }) {
  if (!summary) return <span className="ci-badge ci-badge--none">—</span>;
  const rate = summary.ci_pass_rate;
  if (rate >= 1) return <span className="ci-badge ci-badge--pass">✓</span>;
  if (rate <= 0) return <span className="ci-badge ci-badge--fail">✗</span>;
  return <span className="ci-badge ci-badge--partial">⟳</span>;
}

function StatusDot({ status }: { status: Project["status"] }) {
  return <span className={`status-dot status-dot--${status}`} />;
}

function SortableProjectRow({ project, sessions, tasks, github }: RowProps) {
  const navigate = useNavigate();
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: project.id });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  const ms = getProjectMilestoneStatus(tasks);
  const done = tasks.filter((t) => t.status === "done").length;
  const inProgress = tasks.filter((t) => t.status === "in_progress" || t.status === "assigned").length;
  const todo = tasks.filter((t) => t.status === "pending" || t.status === "blocked").length;

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`project-row${isDragging ? " project-row--dragging" : ""}`}
    >
      {/* drag handle */}
      <span
        className="project-row__handle"
        {...attributes}
        {...listeners}
        aria-label="Drag to reorder"
      >
        ⋮⋮
      </span>

      <StatusDot status={project.status} />

      <span
        className="project-row__name"
        onClick={() => navigate(`/projects/${project.id}`)}
        role="link"
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && navigate(`/projects/${project.id}`)}
      >
        {project.name}
      </span>

      <span className="project-row__milestone">{ms.label}</span>

      <KanbanBar done={done} inProgress={inProgress} todo={todo} />

      <span className="project-row__ace">
        <AceStatusSummary sessions={sessions} projectId={project.id} />
      </span>

      <CIBadgeCompact summary={github} />

      <button
        className="btn btn-sm project-row__open"
        onClick={() => navigate(`/projects/${project.id}`)}
        aria-label={`Open ${project.name}`}
      >
        →
      </button>
    </div>
  );
}

export default function ProjectRowView({
  projects,
  sessions,
  tasks,
  leaders,
  github,
  onReorder,
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
    <div className="project-row-view">
      {reorderError && <div className="view-error">{reorderError}</div>}

      {/* Column headers */}
      <div className="project-row project-row--header">
        <span className="project-row__handle" />
        <span />
        <span className="project-row__name">Project</span>
        <span className="project-row__milestone">Milestone</span>
        <span>Progress</span>
        <span className="project-row__ace">Aces</span>
        <span>CI</span>
        <span />
      </div>

      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={(e) => void handleDragEnd(e)}
      >
        <SortableContext
          items={activeProjects.map((p) => p.id)}
          strategy={verticalListSortingStrategy}
        >
          {activeProjects.map((project) => (
            <SortableProjectRow
              key={project.id}
              project={project}
              sessions={sessions}
              tasks={tasks[project.id] ?? []}
              leader={leaders[project.id]}
              github={github[project.id]}
            />
          ))}
        </SortableContext>
      </DndContext>
    </div>
  );
}

import { useParams } from "react-router-dom";
import { useAppContext } from "../context/AppContext";
import StatusBadge from "../components/common/StatusBadge";
import LeaderConsole from "../components/leader/LeaderConsole";
import TaskBoard from "../components/leader/TaskBoard";
import ContextViewer from "../components/leader/ContextViewer";
import AceList from "../components/ace/AceList";
import "./ProjectView.css";

export default function ProjectView() {
  const { id } = useParams<{ id: string }>();
  const { state, fetchAll } = useAppContext();
  const { projects, sessions, leaders, tasks } = state;

  const project = projects.find((p) => p.id === id);
  const projectSessions = sessions.filter((s) => s.project_id === id);
  const leader = id ? leaders[id] : undefined;
  const projectTasks = id ? (tasks[id] ?? []) : [];

  if (!project) {
    return (
      <div className="project-view" data-testid="project-view">
        <div className="project-view__empty">
          Project not found.
        </div>
      </div>
    );
  }

  return (
    <div className="project-view" data-testid="project-view">
      {/* Header */}
      <div className="project-view__header">
        <div className="project-view__title">
          <h1>{project.name}</h1>
          <StatusBadge status={project.status} />
        </div>
        {project.description && (
          <p className="project-view__desc">{project.description}</p>
        )}
      </div>

      <div className="project-view__layout">
        {/* Left panel — Leader + Context */}
        <aside className="project-view__left">
          <div className="panel">
            <LeaderConsole
              projectId={project.id}
              leader={leader}
              onRefresh={fetchAll}
            />
          </div>

          <div className="panel">
            <ContextViewer leader={leader} />
          </div>
        </aside>

        {/* Right panel — Aces + Tasks */}
        <main className="project-view__right">
          <div className="panel">
            <AceList
              projectId={project.id}
              sessions={projectSessions}
              onRefresh={fetchAll}
            />
          </div>

          <div className="panel">
            <TaskBoard tasks={projectTasks} />
          </div>
        </main>
      </div>
    </div>
  );
}

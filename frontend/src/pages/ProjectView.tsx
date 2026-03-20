import { useParams } from "react-router-dom";
import { useAppContext } from "../context/AppContext";
import StatusBadge from "../components/common/StatusBadge";
import LeaderConsole from "../components/leader/LeaderConsole";
import TaskBoard from "../components/leader/TaskBoard";
import AceList from "../components/ace/AceList";
import ContextHub from "../components/context/ContextHub";
import "./ProjectView.css";

export default function ProjectView() {
  const { id } = useParams<{ id: string }>();
  const { state, fetchAll } = useAppContext();
  const { projects, sessions, leaders, taskGraphs } = state;

  const project = projects.find((p) => p.id === id);
  const projectSessions = sessions.filter((s) => s.project_id === id);
  const leader = id ? leaders[id] : undefined;
  const projectTaskGraphs = id ? (taskGraphs[id] ?? []) : [];

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

      {/* Main 60/40 layout */}
      <div className="project-view__layout">
        {/* Left column — Tasks + Aces */}
        <aside className="project-view__left">
          <div className="panel project-view__tasks">
            <TaskBoard
              projectId={project.id}
              taskGraphs={projectTaskGraphs}
              onRefresh={fetchAll}
            />
          </div>

          <div className="panel project-view__aces">
            <AceList
              projectId={project.id}
              sessions={projectSessions}
              onRefresh={fetchAll}
              compact
            />
          </div>

          <div className="panel project-view__context">
            <ContextHub
              scope="project"
              projectId={project.id}
              showScopeTabs
              availableScopes={["project", "global"]}
            />
          </div>
        </aside>

        {/* Right column — Leader terminal full height */}
        <main className="project-view__right">
          <div className="panel project-view__leader">
            <LeaderConsole
              projectId={project.id}
              leader={leader}
              project={project}
              onRefresh={fetchAll}
            />
          </div>
        </main>
      </div>
    </div>
  );
}

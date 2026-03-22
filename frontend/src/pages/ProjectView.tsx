import { useState } from "react";
import { useParams } from "react-router-dom";
import { useAppContext } from "../context/AppContext";
import StatusBadge from "../components/common/StatusBadge";
import LeaderConsole from "../components/leader/LeaderConsole";
import AceConsole from "../components/ace/AceConsole";
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

  // null = show Leader, string = show that Ace full-screen
  const [expandedAceId, setExpandedAceId] = useState<string | null>(null);

  // If the expanded ace was removed, fall back to leader view
  const expandedAceExists =
    expandedAceId !== null &&
    projectSessions.some((s) => s.id === expandedAceId);
  const activeAceId = expandedAceExists ? expandedAceId : null;

  if (!project) {
    return (
      <div className="project-view" data-testid="project-view">
        <div className="project-view__empty">Project not found.</div>
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
        {/* Left column — Tasks + Aces + Context */}
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
              onExpand={(sessionId) => setExpandedAceId(sessionId)}
              compact
            />
          </div>

          <div className="panel project-view__context">
            <ContextHub
              scope="project"
              projectId={project.id}
              showScopeTabs={false}
            />
          </div>
        </aside>

        {/* Right column — Leader or expanded Ace */}
        <main className="project-view__right">
          <div className="panel project-view__console">
            {activeAceId !== null ? (
              <AceConsole
                projectId={project.id}
                sessions={projectSessions}
                activeAceId={activeAceId}
                onRefresh={fetchAll}
                onSelectAce={(id) => setExpandedAceId(id)}
              />
            ) : (
              <LeaderConsole
                projectId={project.id}
                leader={leader}
                project={project}
                onRefresh={fetchAll}
              />
            )}
          </div>
        </main>
      </div>
    </div>
  );
}

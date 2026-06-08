import { useState } from "react";
import { useParams } from "react-router-dom";
import { useAppContext } from "../context/AppContext";
import StatusBadge from "../components/common/StatusBadge";
import LeaderConsole from "../components/leader/LeaderConsole";
import TaskBoard from "../components/leader/TaskBoard";
import AceList from "../components/ace/AceList";
import AceConsole from "../components/ace/AceConsole";
import ContextHub from "../components/context/ContextHub";
import type { Session } from "../types";
import "./ProjectView.css";

export function getProjectAceSessions(sessions: Session[], projectId?: string) {
  return sessions.filter(
    (s) => s.project_id === projectId && s.session_type === "ace",
  );
}

export default function ProjectView() {
  const { id } = useParams<{ id: string }>();
  const { state, fetchAll } = useAppContext();
  const { projects, sessions, leaders, taskGraphs } = state;

  const project = projects.find((p) => p.id === id);
  const projectAceSessions = getProjectAceSessions(sessions, id);
  const leader = id ? leaders[id] : undefined;
  const projectTaskGraphs = id ? (taskGraphs[id] ?? []) : [];

  // null = collapsed Ace list, string = expanded Ace panel with tabs
  const [expandedAceId, setExpandedAceId] = useState<string | null>(null);

  // If the expanded Ace was removed, collapse back to list
  const expandedAceExists =
    expandedAceId !== null &&
    projectAceSessions.some((s) => s.id === expandedAceId);
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

      <div className="project-view__layout">
        <aside className="project-view__left">
          <div className="panel project-view__leader">
            <LeaderConsole
              projectId={project.id}
              leader={leader}
              project={project}
              onRefresh={fetchAll}
            />
          </div>

          <div className="panel project-view__tasks">
            <TaskBoard
              projectId={project.id}
              taskGraphs={projectTaskGraphs}
              onRefresh={fetchAll}
            />
          </div>

          <div
            className={`panel project-view__aces${activeAceId ? " project-view__aces--expanded" : ""}`}
          >
            {activeAceId ? (
              <AceConsole
                projectId={project.id}
                sessions={projectAceSessions}
                activeAceId={activeAceId}
                onRefresh={fetchAll}
                onSelectAce={(sid) => setExpandedAceId(sid)}
                onCollapse={() => setExpandedAceId(null)}
              />
            ) : (
              <AceList
                projectId={project.id}
                sessions={projectAceSessions}
                onRefresh={fetchAll}
                onExpand={(sid) => setExpandedAceId(sid)}
                compact
              />
            )}
          </div>

          <div className="panel project-view__context">
            <ContextHub
              scope="project"
              projectId={project.id}
              showScopeTabs={false}
            />
          </div>
        </aside>
      </div>
    </div>
  );
}

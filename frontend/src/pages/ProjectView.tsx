import { useParams } from "react-router-dom";
import { useAppContext } from "../context/AppContext";
import StatusBadge from "../components/common/StatusBadge";
import TimeAgo from "../components/common/TimeAgo";
import "./ProjectView.css";

export default function ProjectView() {
  const { id } = useParams<{ id: string }>();
  const { state, dispatch } = useAppContext();
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
        {/* Left panel — Sessions */}
        <aside className="project-view__left panel">
          <h3>Sessions</h3>
          {projectSessions.length === 0 ? (
            <p className="project-view__muted">No sessions yet.</p>
          ) : (
            <ul className="project-view__session-list">
              {projectSessions.map((session) => (
                <li key={session.id}>
                  <button
                    className={`project-view__session-item ${
                      state.selectedSessionId === session.id ? "selected" : ""
                    }`}
                    onClick={() =>
                      dispatch({
                        type: "SELECT_SESSION",
                        payload: session.id,
                      })
                    }
                  >
                    <span className="project-view__session-name">
                      {session.name}
                    </span>
                    <StatusBadge status={session.status} size="sm" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        {/* Right panel — Leader + Tasks */}
        <main className="project-view__right">
          {/* Leader console */}
          <div className="panel project-view__leader">
            <h3>Leader</h3>
            {leader ? (
              <div className="project-view__leader-info">
                <StatusBadge status={leader.status} />
                {leader.goal && (
                  <p className="project-view__leader-goal">{leader.goal}</p>
                )}
                <div className="project-view__leader-meta">
                  Updated <TimeAgo datetime={leader.updated_at} />
                </div>
              </div>
            ) : (
              <p className="project-view__muted">No leader assigned.</p>
            )}
          </div>

          {/* Task board */}
          <div className="panel project-view__tasks">
            <h3>Tasks</h3>
            {projectTasks.length === 0 ? (
              <p className="project-view__muted">No tasks yet.</p>
            ) : (
              <div className="project-view__task-grid">
                {(
                  ["pending", "in_progress", "done"] as const
                ).map((col) => (
                  <div key={col} className="project-view__task-col">
                    <h4>{col.replace(/_/g, " ")}</h4>
                    {projectTasks
                      .filter((t) => t.status === col)
                      .map((task) => (
                        <div
                          key={task.id}
                          className="project-view__task-card"
                        >
                          <span>{task.title}</span>
                          <StatusBadge status={task.status} size="sm" />
                        </div>
                      ))}
                  </div>
                ))}
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}

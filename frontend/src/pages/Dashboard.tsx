import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAppContext } from "../context/AppContext";
import StatusBadge from "../components/common/StatusBadge";
import TimeAgo from "../components/common/TimeAgo";
import CreateProjectModal from "../components/common/CreateProjectModal";
import ConfirmPopover from "../components/common/ConfirmPopover";
import { api, ApiError } from "../utils/api";
import "./Dashboard.css";

export default function Dashboard() {
  const navigate = useNavigate();
  const { state, fetchAll } = useAppContext();
  const { projects, sessions, usage, notifications } = state;
  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeProjects = projects.filter((p) => p.status === "active");
  const archivedProjects = projects.filter((p) => p.status === "archived");

  const handleDelete = async (projectId: string) => {
    try {
      setError(null);
      await api.delete(`/projects/${projectId}`);
      await fetchAll();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Failed to delete project";
      setError(msg);
    }
  };

  const handleArchive = async (projectId: string) => {
    try {
      setError(null);
      await api.patch(`/projects/${projectId}/archive`, {});
      await fetchAll();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Failed to archive project";
      setError(msg);
    }
  };

  return (
    <div className="dashboard" data-testid="dashboard-page">
      <div className="dashboard__header">
        <h1>Dashboard</h1>
        <button
          className="btn btn-primary"
          onClick={() => setShowCreate(true)}
        >
          + New Project
        </button>
      </div>

      <div className="dashboard__grid">
        {/* Cost summary card */}
        <div className="panel dashboard__card">
          <h3 className="dashboard__card-title">Cost</h3>
          <div className="dashboard__stat">
            <span className="dashboard__stat-value">
              ${usage.today_cost.toFixed(2)}
            </span>
            <span className="dashboard__stat-label">today</span>
          </div>
          <div className="dashboard__stat">
            <span className="dashboard__stat-value">
              ${usage.month_cost.toFixed(2)}
            </span>
            <span className="dashboard__stat-label">this month</span>
          </div>
        </div>

        {/* Token summary card */}
        <div className="panel dashboard__card">
          <h3 className="dashboard__card-title">Tokens</h3>
          <div className="dashboard__stat">
            <span className="dashboard__stat-value">
              {formatTokens(usage.today_tokens)}
            </span>
            <span className="dashboard__stat-label">today</span>
          </div>
          <div className="dashboard__stat">
            <span className="dashboard__stat-value">
              {formatTokens(usage.month_tokens)}
            </span>
            <span className="dashboard__stat-label">this month</span>
          </div>
        </div>

        {/* Sessions card */}
        <div className="panel dashboard__card">
          <h3 className="dashboard__card-title">Sessions</h3>
          <div className="dashboard__stat">
            <span className="dashboard__stat-value">{sessions.length}</span>
            <span className="dashboard__stat-label">total</span>
          </div>
          <div className="dashboard__stat">
            <span className="dashboard__stat-value">
              {sessions.filter((s) => s.status === "working").length}
            </span>
            <span className="dashboard__stat-label">active</span>
          </div>
        </div>

        {/* Notifications card */}
        <div className="panel dashboard__card">
          <h3 className="dashboard__card-title">Notifications</h3>
          <div className="dashboard__stat">
            <span className="dashboard__stat-value">
              {notifications.filter((n) => !n.read).length}
            </span>
            <span className="dashboard__stat-label">unread</span>
          </div>
          <div className="dashboard__stat">
            <span className="dashboard__stat-value">
              {notifications.length}
            </span>
            <span className="dashboard__stat-label">total</span>
          </div>
        </div>
      </div>

      {error && (
        <div className="dashboard__error panel" style={{ color: "var(--color-danger)", marginBottom: "var(--space-4)" }}>
          {error}
        </div>
      )}

      {/* Project cards */}
      <section className="dashboard__projects">
        <h2>Projects</h2>
        {activeProjects.length === 0 ? (
          <div className="dashboard__empty">
            <p>No active projects.</p>
            <button
              className="btn btn-primary"
              onClick={() => setShowCreate(true)}
              style={{ marginTop: "var(--space-3)" }}
            >
              Create your first project
            </button>
          </div>
        ) : (
          <div className="dashboard__project-grid">
            {activeProjects.map((project) => (
              <div key={project.id} className="panel dashboard__project-card">
                <div
                  className="dashboard__project-clickable"
                  onClick={() => navigate(`/projects/${project.id}`)}
                >
                  <div className="dashboard__project-header">
                    <h3>{project.name}</h3>
                    <StatusBadge status={project.status} size="sm" />
                  </div>
                  {project.description && (
                    <p className="dashboard__project-desc">
                      {project.description}
                    </p>
                  )}
                  <div className="dashboard__project-meta">
                    <span>
                      {
                        sessions.filter((s) => s.project_id === project.id)
                          .length
                      }{" "}
                      sessions
                    </span>
                    <TimeAgo datetime={project.updated_at} />
                  </div>
                </div>
                <div className="dashboard__project-actions">
                  <button
                    className="btn btn-sm"
                    onClick={() => void handleArchive(project.id)}
                  >
                    Archive
                  </button>
                  <ConfirmPopover
                    message={`Are you sure you want to delete "${project.name}"? This will remove all tasks and context associated with this project.`}
                    confirmLabel="Delete"
                    variant="danger"
                    onConfirm={() => void handleDelete(project.id)}
                  >
                    <button className="btn btn-sm btn-danger">Delete</button>
                  </ConfirmPopover>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Archived projects */}
      {archivedProjects.length > 0 && (
        <section className="dashboard__projects">
          <h2>Archived</h2>
          <div className="dashboard__project-grid">
            {archivedProjects.map((project) => (
              <div key={project.id} className="panel dashboard__project-card dashboard__project-card--archived">
                <div className="dashboard__project-header">
                  <h3>{project.name}</h3>
                  <StatusBadge status={project.status} size="sm" />
                </div>
                {project.description && (
                  <p className="dashboard__project-desc">
                    {project.description}
                  </p>
                )}
                <div className="dashboard__project-actions">
                  <ConfirmPopover
                    message={`Permanently delete "${project.name}"? This cannot be undone.`}
                    confirmLabel="Delete"
                    variant="danger"
                    onConfirm={() => void handleDelete(project.id)}
                  >
                    <button className="btn btn-sm btn-danger">Delete</button>
                  </ConfirmPopover>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <CreateProjectModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={() => {
          void fetchAll();
        }}
      />
    </div>
  );
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}k`;
  return String(n);
}

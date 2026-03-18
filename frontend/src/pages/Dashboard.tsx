import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAppContext } from "../context/AppContext";
import StatusBadge from "../components/common/StatusBadge";
import TimeAgo from "../components/common/TimeAgo";
import CreateProjectModal from "../components/common/CreateProjectModal";
import TowerConsole from "../components/tower/TowerConsole";
import "./Dashboard.css";

export default function Dashboard() {
  const navigate = useNavigate();
  const { state, fetchAll } = useAppContext();
  const { projects, sessions, usage, notifications } = state;
  const [showCreate, setShowCreate] = useState(false);

  const activeProjects = projects.filter((p) => p.status === "active");

  return (
    <div className="dashboard dashboard--two-col" data-testid="dashboard-page">
      {/* Left column: metrics + projects */}
      <div className="dashboard__main">
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
                <button
                  key={project.id}
                  className="panel dashboard__project-card"
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
                </button>
              ))}
            </div>
          )}
        </section>

        <CreateProjectModal
          open={showCreate}
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            void fetchAll();
          }}
        />
      </div>

      {/* Right column: Tower terminal */}
      <div className="dashboard__tower panel">
        <TowerConsole />
      </div>
    </div>
  );
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}k`;
  return String(n);
}

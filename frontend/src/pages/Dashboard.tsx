import { useState } from "react";
import { useAppContext } from "../context/AppContext";
import CreateProjectModal from "../components/common/CreateProjectModal";
import ConfirmPopover from "../components/common/ConfirmPopover";
import ProjectGridView from "../components/dashboard/ProjectGridView";
import ProjectRowView from "../components/dashboard/ProjectRowView";
import ProjectBoardView from "../components/dashboard/ProjectBoardView";
import { api, ApiError } from "../utils/api";
import type { Project } from "../types";
import "./Dashboard.css";

type ViewMode = "grid" | "row" | "board";

const VIEW_PREF_KEY = "atc_dashboard_view";

function loadViewPref(): ViewMode {
  const stored = localStorage.getItem(VIEW_PREF_KEY);
  if (stored === "grid" || stored === "row" || stored === "board") return stored;
  return "grid";
}

export default function Dashboard() {
  const { state, fetchAll } = useAppContext();
  const { projects, sessions, tasks, leaders, github, usage, notifications } = state;

  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<ViewMode>(loadViewPref);
  // Local order state for optimistic drag-to-reorder
  const [orderedProjects, setOrderedProjects] = useState<Project[]>(projects);

  // Sync orderedProjects when global state changes (new project created, etc.)
  const latestIds = projects.map((p) => p.id).join(",");
  const orderedIds = orderedProjects.map((p) => p.id).join(",");
  const displayProjects = latestIds === orderedIds ? orderedProjects : projects;

  const handleViewChange = (v: ViewMode) => {
    setView(v);
    localStorage.setItem(VIEW_PREF_KEY, v);
  };

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

  const handleReorder = (reordered: Project[]) => {
    setOrderedProjects(reordered);
  };

  const handleProjectsChange = (updated: Project[]) => {
    setOrderedProjects(updated);
  };

  const archivedProjects = displayProjects.filter((p) => p.status === "archived");

  return (
    <div className="dashboard" data-testid="dashboard-page">
      <div className="dashboard__header">
        <h1>Dashboard</h1>
        <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
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
            <span className="dashboard__stat-value">{notifications.length}</span>
            <span className="dashboard__stat-label">total</span>
          </div>
        </div>
      </div>

      {error && (
        <div
          className="dashboard__error panel"
          style={{ color: "var(--color-danger)", marginBottom: "var(--space-4)" }}
        >
          {error}
        </div>
      )}

      {/* Projects section with view toggle */}
      <section className="dashboard__projects">
        <div className="dashboard__projects-header">
          <h2>Projects</h2>
          <div className="view-toggle" role="group" aria-label="View mode">
            <button
              className={`view-toggle__btn${view === "grid" ? " view-toggle__btn--active" : ""}`}
              onClick={() => handleViewChange("grid")}
              title="Grid view"
              aria-pressed={view === "grid"}
            >
              ⊞ Grid
            </button>
            <button
              className={`view-toggle__btn${view === "row" ? " view-toggle__btn--active" : ""}`}
              onClick={() => handleViewChange("row")}
              title="Row view"
              aria-pressed={view === "row"}
            >
              ☰ Row
            </button>
            <button
              className={`view-toggle__btn${view === "board" ? " view-toggle__btn--active" : ""}`}
              onClick={() => handleViewChange("board")}
              title="Board view"
              aria-pressed={view === "board"}
            >
              ▦ Board
            </button>
          </div>
        </div>

        {displayProjects.filter((p) => p.status !== "archived").length === 0 &&
        view !== "board" ? (
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
        ) : view === "grid" ? (
          <ProjectGridView
            projects={displayProjects}
            sessions={sessions}
            tasks={tasks}
            leaders={leaders}
            github={github}
            onReorder={handleReorder}
            onArchive={handleArchive}
            onDelete={handleDelete}
          />
        ) : view === "row" ? (
          <ProjectRowView
            projects={displayProjects}
            sessions={sessions}
            tasks={tasks}
            leaders={leaders}
            github={github}
            onReorder={handleReorder}
          />
        ) : (
          <ProjectBoardView
            projects={displayProjects}
            sessions={sessions}
            tasks={tasks}
            leaders={leaders}
            github={github}
            onProjectsChange={handleProjectsChange}
          />
        )}
      </section>

      {/* Archived projects — only shown outside board view (board has its own column) */}
      {view !== "board" && archivedProjects.length > 0 && (
        <section className="dashboard__projects" style={{ marginTop: "var(--space-8)" }}>
          <h2>Archived</h2>
          <div className="dashboard__project-grid">
            {archivedProjects.map((project) => (
              <div
                key={project.id}
                className="panel dashboard__project-card dashboard__project-card--archived"
              >
                <div className="dashboard__project-header">
                  <h3>{project.name}</h3>
                </div>
                {project.description && (
                  <p className="dashboard__project-desc">{project.description}</p>
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

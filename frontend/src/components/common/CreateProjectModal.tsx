import { useState, useRef, useEffect } from "react";
import { api } from "../../utils/api";
import type { Project } from "../../types";
import "./CreateProjectModal.css";

const GITHUB_ORG_KEY = "atc:github_default_org";

interface CreateProjectModalProps {
  open: boolean;
  onClose: () => void;
  onCreated: (project: Project) => void;
}

export default function CreateProjectModal({
  open,
  onClose,
  onCreated,
}: CreateProjectModalProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [repoPath, setRepoPath] = useState("");
  const [githubRepo, setGithubRepo] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  const defaultOrg = localStorage.getItem(GITHUB_ORG_KEY) ?? "";

  useEffect(() => {
    if (open) {
      setName("");
      setDescription("");
      setRepoPath("");
      setGithubRepo(defaultOrg ? `${defaultOrg}/` : "");
      setError(null);
      setTimeout(() => nameRef.current?.focus(), 50);
    }
  }, [open, defaultOrg]);

  useEffect(() => {
    if (!open) return;
    function handleEscape(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [open, onClose]);

  if (!open) return null;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setError("Project name is required");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const project = await api.post<Project>("/projects", {
        name: name.trim(),
        description: description.trim() || null,
        repo_path: repoPath.trim() || null,
        github_repo: githubRepo.trim() || null,
      });
      onCreated(project);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create project");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal panel"
        onClick={(e) => e.stopPropagation()}
        data-testid="create-project-modal"
      >
        <div className="modal__header">
          <h2>Create Project</h2>
          <button className="modal__close" onClick={onClose} aria-label="Close">
            &times;
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal__body">
            {error && <div className="modal__error">{error}</div>}

            <div className="form-group">
              <label htmlFor="project-name">Name *</label>
              <input
                ref={nameRef}
                id="project-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="my-project"
                required
              />
            </div>

            <div className="form-group">
              <label htmlFor="project-desc">Description</label>
              <textarea
                id="project-desc"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="What this project does..."
                rows={2}
              />
            </div>

            <div className="form-group">
              <label htmlFor="project-repo">Repository Path</label>
              <input
                id="project-repo"
                type="text"
                value={repoPath}
                onChange={(e) => setRepoPath(e.target.value)}
                placeholder="/path/to/local/repo"
              />
              <span className="form-hint">
                Local clone path. Leave blank for net-new projects.
              </span>
            </div>

            <div className="form-group">
              <label htmlFor="project-github">GitHub Repo</label>
              <input
                id="project-github"
                type="text"
                value={githubRepo}
                onChange={(e) => setGithubRepo(e.target.value)}
                placeholder={defaultOrg ? `${defaultOrg}/repo-name` : "owner/repo"}
              />
              <span className="form-hint">
                Optional. A repo will be created after planning if left blank.
              </span>
            </div>
          </div>

          <div className="modal__footer">
            <button type="button" className="btn" onClick={onClose}>
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={submitting}
            >
              {submitting ? "Creating..." : "Create Project"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

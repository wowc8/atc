import { useState, useRef } from "react";
import { useAppContext } from "../context/AppContext";
import { api } from "../utils/api";
import "./SettingsPage.css";

const GITHUB_ORG_KEY = "atc:github_default_org";

type ExportStatus = "idle" | "exporting" | "done" | "error";
type ImportStatus = "idle" | "importing" | "done" | "error";

interface ImportResult {
  project_id?: string;
  project_name?: string;
  auto_backup_path?: string | null;
  imported_projects?: { id: string; name: string }[];
  auto_backup_paths?: string[];
}

export default function SettingsPage() {
  const { state } = useAppContext();
  const [backendUrl] = useState("http://127.0.0.1:8420");
  const [githubOrg, setGithubOrg] = useState(
    () => localStorage.getItem(GITHUB_ORG_KEY) ?? "",
  );

  // Export state
  const [exportStatus, setExportStatus] = useState<ExportStatus>("idle");
  const [exportError, setExportError] = useState("");
  const [selectedExportProjectId, setSelectedExportProjectId] = useState("");

  // Import state
  const [importStatus, setImportStatus] = useState<ImportStatus>("idle");
  const [importError, setImportError] = useState("");
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const importFileRef = useRef<HTMLInputElement>(null);
  const importAllFileRef = useRef<HTMLInputElement>(null);

  // Restore confirm state
  const [restoreConfirm, setRestoreConfirm] = useState<{
    file: File;
    projectId: string;
  } | null>(null);

  function handleGithubOrgChange(value: string) {
    setGithubOrg(value);
    if (value.trim()) {
      localStorage.setItem(GITHUB_ORG_KEY, value.trim());
    } else {
      localStorage.removeItem(GITHUB_ORG_KEY);
    }
  }

  async function handleExportProject(projectId: string) {
    setExportStatus("exporting");
    setExportError("");
    try {
      const blob = await api.postBlob(`/settings/export/${projectId}`);
      const project = state.projects.find((p) => p.id === projectId);
      const name = project?.name ?? "project";
      const safeName = name.replace(/[^a-zA-Z0-9_-]/g, "_");
      const date = new Date().toISOString().slice(0, 10);
      downloadBlob(blob, `${safeName}-${date}.atc-backup.zip`);
      setExportStatus("done");
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "Export failed");
      setExportStatus("error");
    }
  }

  async function handleExportAll() {
    setExportStatus("exporting");
    setExportError("");
    try {
      const blob = await api.postBlob("/settings/export-all");
      const date = new Date().toISOString().slice(0, 10);
      downloadBlob(blob, `atc-full-backup-${date}.atc-backup.zip`);
      setExportStatus("done");
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "Export failed");
      setExportStatus("error");
    }
  }

  async function handleImportFile(file: File, targetProjectId?: string) {
    setImportStatus("importing");
    setImportError("");
    setImportResult(null);
    try {
      const b64 = await fileToBase64(file);
      const result = await api.post<ImportResult>("/settings/import", {
        data: b64,
        target_project_id: targetProjectId ?? null,
      });
      setImportResult(result);
      setImportStatus("done");
    } catch (e) {
      setImportError(e instanceof Error ? e.message : "Import failed");
      setImportStatus("error");
    }
  }

  async function handleImportAllFile(file: File) {
    setImportStatus("importing");
    setImportError("");
    setImportResult(null);
    try {
      const b64 = await fileToBase64(file);
      const result = await api.post<ImportResult>("/settings/import-all", {
        data: b64,
      });
      setImportResult(result);
      setImportStatus("done");
    } catch (e) {
      setImportError(e instanceof Error ? e.message : "Import failed");
      setImportStatus("error");
    }
  }

  function onImportFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) handleImportFile(file);
    e.target.value = "";
  }

  function onImportAllFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) handleImportAllFile(file);
    e.target.value = "";
  }

  function onRestoreFileSelected(
    e: React.ChangeEvent<HTMLInputElement>,
    projectId: string,
  ) {
    const file = e.target.files?.[0];
    if (file) {
      setRestoreConfirm({ file, projectId });
    }
    e.target.value = "";
  }

  function confirmRestore() {
    if (!restoreConfirm) return;
    handleImportFile(restoreConfirm.file, restoreConfirm.projectId);
    setRestoreConfirm(null);
  }

  return (
    <div className="settings-page" data-testid="settings-page">
      <h1>Settings</h1>

      <div className="settings-page__grid">
        <section className="panel settings-page__section">
          <h2>Connection</h2>
          <div className="form-group">
            <label htmlFor="backend-url">Backend URL</label>
            <input id="backend-url" type="text" value={backendUrl} readOnly />
          </div>
          <div className="settings-page__status">
            <span className="settings-page__dot settings-page__dot--connected" />
            Connected
          </div>
        </section>

        <section className="panel settings-page__section">
          <h2>GitHub Defaults</h2>
          <div className="form-group">
            <label htmlFor="github-org">Default Org / Username</label>
            <input
              id="github-org"
              type="text"
              value={githubOrg}
              onChange={(e) => handleGithubOrgChange(e.target.value)}
              placeholder="my-org"
            />
            <span className="form-hint">
              Pre-fills the GitHub Repo field when creating new projects.
            </span>
          </div>
        </section>

        <section className="panel settings-page__section">
          <h2>Tower Status</h2>
          <div className="settings-page__info-row">
            <span className="settings-page__label">Status</span>
            <span className="settings-page__value">
              {state.brainStatus.status}
            </span>
          </div>
          <div className="settings-page__info-row">
            <span className="settings-page__label">Active Projects</span>
            <span className="settings-page__value">
              {state.brainStatus.active_projects}
            </span>
          </div>
        </section>

        <section className="panel settings-page__section">
          <h2>Appearance</h2>
          <div className="settings-page__info-row">
            <span className="settings-page__label">Theme</span>
            <span className="settings-page__value">Dark</span>
          </div>
        </section>

        {/* Export Section */}
        <section
          className="panel settings-page__section"
          data-testid="export-section"
        >
          <h2>Export</h2>
          <p className="settings-page__description">
            Download a backup of your project data as a .atc-backup.zip file.
          </p>

          <div className="settings-page__export-actions">
            {state.projects.length > 0 && (
              <div className="settings-page__export-group">
                <label htmlFor="export-project-select">Export Project</label>
                <div className="settings-page__row">
                  <select
                    id="export-project-select"
                    data-testid="export-project-select"
                    value={selectedExportProjectId}
                    onChange={(e) => setSelectedExportProjectId(e.target.value)}
                    disabled={exportStatus === "exporting"}
                  >
                    <option value="" disabled>
                      Select a project...
                    </option>
                    {state.projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                  <button
                    className="btn"
                    onClick={() => {
                      if (selectedExportProjectId) {
                        handleExportProject(selectedExportProjectId);
                        setSelectedExportProjectId("");
                      }
                    }}
                    disabled={
                      exportStatus === "exporting" || !selectedExportProjectId
                    }
                    data-testid="export-project-btn"
                  >
                    Export
                  </button>
                </div>
              </div>
            )}

            <button
              className="btn btn-primary"
              onClick={() => handleExportAll()}
              disabled={exportStatus === "exporting"}
              data-testid="export-all-btn"
            >
              {exportStatus === "exporting" ? "Exporting..." : "Export All"}
            </button>
          </div>

          {exportStatus === "exporting" && (
            <div
              className="settings-page__progress"
              data-testid="export-progress"
            >
              <div className="settings-page__progress-bar">
                <div className="settings-page__progress-fill settings-page__progress-fill--indeterminate" />
              </div>
            </div>
          )}
          {exportStatus === "done" && (
            <p className="settings-page__success" data-testid="export-success">
              Export complete. Check your downloads.
            </p>
          )}
          {exportError && (
            <p className="settings-page__error" data-testid="export-error">
              {exportError}
            </p>
          )}
        </section>

        {/* Import Section */}
        <section
          className="panel settings-page__section"
          data-testid="import-section"
        >
          <h2>Import</h2>
          <p className="settings-page__description">
            Restore from a .atc-backup.zip file. Importing always creates a new
            project.
          </p>

          <div className="settings-page__import-actions">
            <div className="settings-page__import-group">
              <button
                className="btn"
                onClick={() => importFileRef.current?.click()}
                disabled={importStatus === "importing"}
                data-testid="import-project-btn"
              >
                Import Project
              </button>
              <input
                ref={importFileRef}
                type="file"
                accept=".zip"
                className="settings-page__file-input"
                onChange={onImportFileSelected}
                data-testid="import-project-file"
              />
            </div>

            <div className="settings-page__import-group">
              <button
                className="btn"
                onClick={() => importAllFileRef.current?.click()}
                disabled={importStatus === "importing"}
                data-testid="import-all-btn"
              >
                Import All
              </button>
              <input
                ref={importAllFileRef}
                type="file"
                accept=".zip"
                className="settings-page__file-input"
                onChange={onImportAllFileSelected}
                data-testid="import-all-file"
              />
            </div>
          </div>

          {/* Restore into existing project */}
          {state.projects.length > 0 && (
            <div className="settings-page__restore-group">
              <label htmlFor="restore-project-select">
                Restore into Existing Project
              </label>
              <span className="form-hint">
                Current data will be auto-backed up before replacing.
              </span>
              <div className="settings-page__row">
                <select
                  id="restore-project-select"
                  data-testid="restore-project-select"
                  defaultValue=""
                  onChange={(e) => {
                    if (!e.target.value) return;
                    const pid = e.target.value;
                    // Create a hidden file input for this restore
                    const input = document.createElement("input");
                    input.type = "file";
                    input.accept = ".zip";
                    input.onchange = (ev) => {
                      const file = (ev.target as HTMLInputElement).files?.[0];
                      if (file) setRestoreConfirm({ file, projectId: pid });
                    };
                    input.click();
                    e.target.value = "";
                  }}
                  disabled={importStatus === "importing"}
                >
                  <option value="" disabled>
                    Select project to restore into...
                  </option>
                  {state.projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          )}

          {importStatus === "importing" && (
            <div
              className="settings-page__progress"
              data-testid="import-progress"
            >
              <div className="settings-page__progress-bar">
                <div className="settings-page__progress-fill settings-page__progress-fill--indeterminate" />
              </div>
            </div>
          )}
          {importStatus === "done" && importResult && (
            <div
              className="settings-page__success"
              data-testid="import-success"
            >
              <p>Import complete.</p>
              {importResult.project_name && (
                <p>
                  Project &ldquo;{importResult.project_name}&rdquo; created.
                </p>
              )}
              {importResult.imported_projects &&
                importResult.imported_projects.length > 0 && (
                  <p>
                    {importResult.imported_projects.length} project(s) imported.
                  </p>
                )}
              {importResult.auto_backup_path && (
                <p className="form-hint">
                  Previous data backed up to: {importResult.auto_backup_path}
                </p>
              )}
            </div>
          )}
          {importError && (
            <p className="settings-page__error" data-testid="import-error">
              {importError}
            </p>
          )}
        </section>
      </div>

      {/* Restore Confirmation Dialog */}
      {restoreConfirm && (
        <div
          className="settings-page__overlay"
          data-testid="restore-confirm-dialog"
        >
          <div className="settings-page__dialog panel">
            <h3>Confirm Restore</h3>
            <p>
              This will replace all data in the selected project. The current
              data will be automatically backed up before replacing.
            </p>
            <p className="form-hint">
              Backup will be saved to ~/Library/Application
              Support/com.atc/backups/
            </p>
            <div className="settings-page__dialog-actions">
              <button className="btn" onClick={() => setRestoreConfirm(null)}>
                Cancel
              </button>
              <button
                className="btn btn-danger"
                onClick={confirmRestore}
                data-testid="restore-confirm-btn"
              >
                Replace & Restore
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      // Strip data URL prefix (e.g. "data:application/zip;base64,")
      const b64 = result.includes(",") ? result.split(",")[1] : result;
      resolve(b64);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

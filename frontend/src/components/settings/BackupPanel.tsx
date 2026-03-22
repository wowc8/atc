import { useCallback, useEffect, useState } from "react";
import { api } from "../../utils/api";

interface BackupStatus {
  auto_backup_enabled: boolean;
  auto_backup_interval_hours: number;
  local_backup_dir: string;
  keep_last_n: number;
  dropbox_enabled: boolean;
  dropbox_connected: boolean;
  gdrive_enabled: boolean;
  gdrive_connected: boolean;
  last_backup_at: string | null;
  last_backup_size_bytes: number | null;
}

interface BackupLogEntry {
  id: string;
  backup_type: string;
  status: string;
  path: string | null;
  size_bytes: number | null;
  error: string | null;
  created_at: string;
}

interface CreateBackupResponse {
  ok: boolean;
  path: string;
  size_bytes: number;
  created_at: string;
  entry_counts: Record<string, number>;
}

interface RestoreResponse {
  ok: boolean;
  projects_count: number;
  sessions_count: number;
  memories_count: number;
  rebuilt_embeddings: number;
}

interface AuthUrlResponse {
  url: string;
}

interface ConnectResponse {
  ok: boolean;
  message: string;
}

function formatBytes(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)} MB`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)} KB`;
  return `${n} B`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

export function BackupPanel() {
  const [status, setStatus] = useState<BackupStatus | null>(null);
  const [recentBackups, setRecentBackups] = useState<BackupLogEntry[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [creating, setCreating] = useState(false);
  const [createResult, setCreateResult] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);

  const [restorePath, setRestorePath] = useState("");
  const [restoring, setRestoring] = useState(false);
  const [restoreResult, setRestoreResult] = useState<string | null>(null);
  const [restoreError, setRestoreError] = useState<string | null>(null);

  const [dropboxAuthUrl, setDropboxAuthUrl] = useState<string | null>(null);
  const [dropboxCode, setDropboxCode] = useState("");
  const [dropboxConnecting, setDropboxConnecting] = useState(false);
  const [dropboxMsg, setDropboxMsg] = useState<string | null>(null);
  const [dropboxErr, setDropboxErr] = useState<string | null>(null);

  const [gdriveAuthUrl, setGdriveAuthUrl] = useState<string | null>(null);
  const [gdriveCode, setGdriveCode] = useState("");
  const [gdriveConnecting, setGdriveConnecting] = useState(false);
  const [gdriveMsg, setGdriveMsg] = useState<string | null>(null);
  const [gdriveErr, setGdriveErr] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const [s, logs] = await Promise.all([
        api.get<BackupStatus>("/backup/status"),
        api.get<BackupLogEntry[]>("/backup/list"),
      ]);
      setStatus(s);
      setRecentBackups(logs);
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Failed to load backup status");
    }
  }, []);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  const handleCreate = useCallback(async () => {
    setCreating(true);
    setCreateResult(null);
    setCreateError(null);
    try {
      const res = await api.post<CreateBackupResponse>("/backup/create", {
        destination: "local",
      });
      setCreateResult(`Backup saved: ${res.path} (${formatBytes(res.size_bytes)})`);
      await loadStatus();
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : "Backup failed");
    } finally {
      setCreating(false);
    }
  }, [loadStatus]);

  const handleRestore = useCallback(async () => {
    if (!restorePath.trim()) return;
    setRestoring(true);
    setRestoreResult(null);
    setRestoreError(null);
    try {
      const res = await api.post<RestoreResponse>("/backup/restore", {
        path: restorePath.trim(),
      });
      setRestoreResult(
        `Restored: ${res.projects_count} projects, ${res.sessions_count} sessions, ` +
          `${res.memories_count} memories, ${res.rebuilt_embeddings} embeddings rebuilt`,
      );
    } catch (e) {
      setRestoreError(e instanceof Error ? e.message : "Restore failed");
    } finally {
      setRestoring(false);
    }
  }, [restorePath]);

  const handleDropboxAuthUrl = useCallback(async () => {
    setDropboxMsg(null);
    setDropboxErr(null);
    try {
      const res = await api.get<AuthUrlResponse>("/backup/dropbox/auth-url");
      setDropboxAuthUrl(res.url);
    } catch (e) {
      setDropboxErr(e instanceof Error ? e.message : "Failed to get auth URL");
    }
  }, []);

  const handleDropboxConnect = useCallback(async () => {
    if (!dropboxCode.trim()) return;
    setDropboxConnecting(true);
    setDropboxMsg(null);
    setDropboxErr(null);
    try {
      const res = await api.post<ConnectResponse>("/backup/dropbox/connect", {
        code: dropboxCode.trim(),
      });
      setDropboxMsg(res.message);
      setDropboxAuthUrl(null);
      setDropboxCode("");
      await loadStatus();
    } catch (e) {
      setDropboxErr(e instanceof Error ? e.message : "Connection failed");
    } finally {
      setDropboxConnecting(false);
    }
  }, [dropboxCode, loadStatus]);

  const handleGdriveAuthUrl = useCallback(async () => {
    setGdriveMsg(null);
    setGdriveErr(null);
    try {
      const res = await api.get<AuthUrlResponse>("/backup/gdrive/auth-url");
      setGdriveAuthUrl(res.url);
    } catch (e) {
      setGdriveErr(e instanceof Error ? e.message : "Failed to get auth URL");
    }
  }, []);

  const handleGdriveConnect = useCallback(async () => {
    if (!gdriveCode.trim()) return;
    setGdriveConnecting(true);
    setGdriveMsg(null);
    setGdriveErr(null);
    try {
      const res = await api.post<ConnectResponse>("/backup/gdrive/connect", {
        code: gdriveCode.trim(),
      });
      setGdriveMsg(res.message);
      setGdriveAuthUrl(null);
      setGdriveCode("");
      await loadStatus();
    } catch (e) {
      setGdriveErr(e instanceof Error ? e.message : "Connection failed");
    } finally {
      setGdriveConnecting(false);
    }
  }, [gdriveCode, loadStatus]);

  return (
    <section className="panel settings-page__section" data-testid="backup-section">
      <h2>Backup</h2>
      <p className="settings-page__description">
        Full-system .atcb backups include the database and config (secrets excluded).
      </p>

      {loadError && (
        <p className="settings-page__error" data-testid="backup-load-error">
          {loadError}
        </p>
      )}

      {/* Status */}
      {status && (
        <div className="backup__status-row">
          <span className="settings-page__label">Last backup</span>
          <span className="settings-page__value">
            {status.last_backup_at
              ? `${formatDate(status.last_backup_at)}${status.last_backup_size_bytes ? ` — ${formatBytes(status.last_backup_size_bytes)}` : ""}`
              : "Never"}
          </span>
        </div>
      )}

      {status && (
        <div className="backup__status-row">
          <span className="settings-page__label">Auto-backup</span>
          <span className="settings-page__value">
            {status.auto_backup_enabled
              ? `Every ${status.auto_backup_interval_hours}h → ${status.local_backup_dir} (keep last ${status.keep_last_n})`
              : "Disabled"}
          </span>
        </div>
      )}

      {/* Create */}
      <div className="backup__action">
        <button
          className="btn"
          onClick={handleCreate}
          disabled={creating}
          data-testid="backup-create-btn"
        >
          {creating ? "Creating..." : "Create Backup Now"}
        </button>
        {createResult && (
          <p className="settings-page__description" data-testid="backup-create-result">
            {createResult}
          </p>
        )}
        {createError && (
          <p className="settings-page__error" data-testid="backup-create-error">
            {createError}
          </p>
        )}
      </div>

      {/* Restore */}
      <div className="backup__action">
        <h3>Restore from File</h3>
        <div className="form-group">
          <input
            type="text"
            value={restorePath}
            onChange={(e) => setRestorePath(e.target.value)}
            placeholder="/path/to/atc-backup.atcb"
            data-testid="backup-restore-path"
          />
        </div>
        <button
          className="btn btn--danger"
          onClick={handleRestore}
          disabled={restoring || !restorePath.trim()}
          data-testid="backup-restore-btn"
        >
          {restoring ? "Restoring..." : "Restore"}
        </button>
        {restoreResult && (
          <p className="settings-page__description" data-testid="backup-restore-result">
            {restoreResult}
          </p>
        )}
        {restoreError && (
          <p className="settings-page__error" data-testid="backup-restore-error">
            {restoreError}
          </p>
        )}
      </div>

      {/* Dropbox */}
      <div className="backup__action">
        <h3>
          Dropbox{" "}
          {status?.dropbox_connected && (
            <span className="backup__connected-badge">Connected</span>
          )}
        </h3>
        {!status?.dropbox_connected && (
          <>
            <button
              className="btn"
              onClick={handleDropboxAuthUrl}
              data-testid="dropbox-auth-url-btn"
            >
              Connect Dropbox
            </button>
            {dropboxAuthUrl && (
              <>
                <p className="settings-page__description">
                  Visit this URL to authorize ATC, then paste the code below:
                </p>
                <a
                  href={dropboxAuthUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="backup__auth-link"
                  data-testid="dropbox-auth-link"
                >
                  {dropboxAuthUrl}
                </a>
                <div className="form-group">
                  <input
                    type="text"
                    value={dropboxCode}
                    onChange={(e) => setDropboxCode(e.target.value)}
                    placeholder="Paste authorization code"
                    data-testid="dropbox-code-input"
                  />
                </div>
                <button
                  className="btn"
                  onClick={handleDropboxConnect}
                  disabled={dropboxConnecting || !dropboxCode.trim()}
                  data-testid="dropbox-connect-btn"
                >
                  {dropboxConnecting ? "Connecting..." : "Confirm"}
                </button>
              </>
            )}
          </>
        )}
        {dropboxMsg && (
          <p className="settings-page__description" data-testid="dropbox-msg">
            {dropboxMsg}
          </p>
        )}
        {dropboxErr && (
          <p className="settings-page__error" data-testid="dropbox-err">
            {dropboxErr}
          </p>
        )}
      </div>

      {/* Google Drive */}
      <div className="backup__action">
        <h3>
          Google Drive{" "}
          {status?.gdrive_connected && (
            <span className="backup__connected-badge">Connected</span>
          )}
        </h3>
        {!status?.gdrive_connected && (
          <>
            <button
              className="btn"
              onClick={handleGdriveAuthUrl}
              data-testid="gdrive-auth-url-btn"
            >
              Connect Google Drive
            </button>
            {gdriveAuthUrl && (
              <>
                <p className="settings-page__description">
                  Visit this URL to authorize ATC, then paste the code below:
                </p>
                <a
                  href={gdriveAuthUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="backup__auth-link"
                  data-testid="gdrive-auth-link"
                >
                  {gdriveAuthUrl}
                </a>
                <div className="form-group">
                  <input
                    type="text"
                    value={gdriveCode}
                    onChange={(e) => setGdriveCode(e.target.value)}
                    placeholder="Paste authorization code"
                    data-testid="gdrive-code-input"
                  />
                </div>
                <button
                  className="btn"
                  onClick={handleGdriveConnect}
                  disabled={gdriveConnecting || !gdriveCode.trim()}
                  data-testid="gdrive-connect-btn"
                >
                  {gdriveConnecting ? "Connecting..." : "Confirm"}
                </button>
              </>
            )}
          </>
        )}
        {gdriveMsg && (
          <p className="settings-page__description" data-testid="gdrive-msg">
            {gdriveMsg}
          </p>
        )}
        {gdriveErr && (
          <p className="settings-page__error" data-testid="gdrive-err">
            {gdriveErr}
          </p>
        )}
      </div>

      {/* Recent backups */}
      {recentBackups.length > 0 && (
        <div className="backup__recent">
          <h3>Recent Backups</h3>
          <div className="backup__log-list">
            {recentBackups.slice(0, 10).map((entry) => (
              <div
                key={entry.id}
                className={`backup__log-entry backup__log-entry--${entry.status}`}
                data-testid="backup-log-entry"
              >
                <span className="backup__log-time">{formatDate(entry.created_at)}</span>
                <span className="backup__log-type">{entry.backup_type}</span>
                <span className="backup__log-status">{entry.status}</span>
                {entry.size_bytes != null && (
                  <span className="backup__log-size">{formatBytes(entry.size_bytes)}</span>
                )}
                {entry.path && (
                  <span className="backup__log-path" title={entry.path}>
                    {entry.path.split("/").pop()}
                  </span>
                )}
                {entry.error && (
                  <span className="backup__log-error" title={entry.error}>
                    {entry.error}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

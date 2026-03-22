"""Backup REST endpoints.

Routes:
  GET  /api/backup/list              → list recent backups from backup_log
  POST /api/backup/create            → create backup now
  POST /api/backup/restore           → restore from file path
  GET  /api/backup/dropbox/auth-url  → get Dropbox OAuth URL
  POST /api/backup/dropbox/connect   → exchange Dropbox auth code
  GET  /api/backup/gdrive/auth-url   → get Google Drive OAuth URL
  POST /api/backup/gdrive/connect    → exchange Google Drive auth code
  GET  /api/backup/status            → auto-backup config + last backup time
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateBackupRequest(BaseModel):
    destination: str = "local"  # local | dropbox | gdrive


class RestoreRequest(BaseModel):
    path: str


class ConnectDropboxRequest(BaseModel):
    code: str


class ConnectGDriveRequest(BaseModel):
    code: str


class BackupLogEntry(BaseModel):
    id: str
    backup_type: str
    status: str
    path: str | None = None
    size_bytes: int | None = None
    error: str | None = None
    created_at: str


class BackupStatusResponse(BaseModel):
    auto_backup_enabled: bool
    auto_backup_interval_hours: int
    local_backup_dir: str
    keep_last_n: int
    dropbox_enabled: bool
    dropbox_connected: bool
    gdrive_enabled: bool
    gdrive_connected: bool
    last_backup_at: str | None = None
    last_backup_size_bytes: int | None = None


class CreateBackupResponse(BaseModel):
    ok: bool
    path: str
    size_bytes: int
    created_at: str
    entry_counts: dict[str, int]


class RestoreResponse(BaseModel):
    ok: bool
    projects_count: int
    sessions_count: int
    memories_count: int
    rebuilt_embeddings: int


class AuthUrlResponse(BaseModel):
    url: str


class ConnectResponse(BaseModel):
    ok: bool
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uuid4() -> str:
    return str(uuid.uuid4())


async def _log_backup(
    db: Any,
    backup_type: str,
    status: str,
    *,
    path: str | None = None,
    size_bytes: int | None = None,
    error: str | None = None,
) -> None:
    """Insert a row into backup_log."""
    try:
        await db.execute(
            """INSERT INTO backup_log (id, backup_type, status, path, size_bytes, error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (_uuid4(), backup_type, status, path, size_bytes, error, _now()),
        )
        await db.commit()
    except Exception as exc:
        logger.warning("Failed to write backup_log entry: %s", exc)


def _get_backup_service(request: Request) -> Any:
    svc = getattr(request.app.state, "backup_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Backup service not available")
    return svc


def _get_settings(request: Request) -> Any:
    return request.app.state.settings


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/list", response_model=list[BackupLogEntry])
async def list_backups(request: Request) -> list[BackupLogEntry]:
    """Return the most recent 50 backup log entries."""
    db = request.app.state.db
    cursor = await db.execute(
        """SELECT id, backup_type, status, path, size_bytes, error, created_at
           FROM backup_log
           ORDER BY created_at DESC
           LIMIT 50"""
    )
    rows = await cursor.fetchall()
    return [
        BackupLogEntry(
            id=r["id"],
            backup_type=r["backup_type"],
            status=r["status"],
            path=r["path"],
            size_bytes=r["size_bytes"],
            error=r["error"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.post("/create", response_model=CreateBackupResponse)
async def create_backup(
    body: CreateBackupRequest,
    request: Request,
) -> CreateBackupResponse:
    """Create a new backup immediately."""
    svc = _get_backup_service(request)
    db = request.app.state.db
    settings = _get_settings(request)
    backup_cfg = settings.backup

    try:
        result = await svc.create()
    except Exception as exc:
        await _log_backup(db, body.destination, "failed", error=str(exc))
        logger.exception("Backup creation failed")
        raise HTTPException(status_code=500, detail=f"Backup failed: {exc}") from exc

    await _log_backup(
        db,
        body.destination,
        "success",
        path=str(result.path),
        size_bytes=result.size_bytes,
    )

    # Optionally sync to cloud
    if body.destination == "dropbox" and backup_cfg.dropbox_enabled:
        try:
            from atc.backup.dropbox_sync import DropboxSync

            sync = DropboxSync(
                app_key=getattr(backup_cfg, "dropbox_app_key", ""),
                app_secret=getattr(backup_cfg, "dropbox_app_secret", ""),
                refresh_token=backup_cfg.dropbox_refresh_token,
            )
            remote = f"/ATC Backups/{result.path.name}"
            await __import__("asyncio").to_thread(sync.upload, result.path, remote)
            await _log_backup(db, "dropbox", "success", path=remote, size_bytes=result.size_bytes)
        except Exception as exc:
            logger.warning("Dropbox sync failed: %s", exc)
            await _log_backup(db, "dropbox", "failed", error=str(exc))

    elif body.destination == "gdrive" and backup_cfg.gdrive_enabled:
        try:
            from atc.backup.gdrive_sync import GoogleDriveSync

            gdrive_sync = GoogleDriveSync(
                client_id=getattr(backup_cfg, "gdrive_client_id", ""),
                client_secret=getattr(backup_cfg, "gdrive_client_secret", ""),
                credentials=backup_cfg.gdrive_credentials,
            )
            folder_id = backup_cfg.gdrive_folder_id or "root"
            await __import__("asyncio").to_thread(gdrive_sync.upload, result.path, folder_id)
            await _log_backup(
                db, "gdrive", "success",
                path=f"gdrive:{folder_id}/{result.path.name}",
                size_bytes=result.size_bytes,
            )
        except Exception as exc:
            logger.warning("Google Drive sync failed: %s", exc)
            await _log_backup(db, "gdrive", "failed", error=str(exc))

    return CreateBackupResponse(
        ok=True,
        path=str(result.path),
        size_bytes=result.size_bytes,
        created_at=result.created_at,
        entry_counts=result.entry_counts,
    )


@router.post("/restore", response_model=RestoreResponse)
async def restore_backup(
    body: RestoreRequest,
    request: Request,
) -> RestoreResponse:
    """Restore ATC from a .atcb backup file path."""
    svc = _get_backup_service(request)
    db = request.app.state.db
    backup_path = Path(body.path)

    if not backup_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Backup file not found: {body.path}",
        )
    if not backup_path.suffix == ".atcb":
        raise HTTPException(
            status_code=400,
            detail="Only .atcb files are supported for restore",
        )

    try:
        result = await svc.restore(backup_path)
    except ValueError as exc:
        await _log_backup(db, "local", "failed", path=body.path, error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        await _log_backup(db, "local", "failed", path=body.path, error=str(exc))
        logger.exception("Restore failed")
        raise HTTPException(status_code=500, detail=f"Restore failed: {exc}") from exc

    await _log_backup(db, "local", "success", path=body.path)

    return RestoreResponse(
        ok=True,
        projects_count=result.projects_count,
        sessions_count=result.sessions_count,
        memories_count=result.memories_count,
        rebuilt_embeddings=result.rebuilt_embeddings,
    )


# ---------------------------------------------------------------------------
# Dropbox OAuth
# ---------------------------------------------------------------------------


@router.get("/dropbox/auth-url", response_model=AuthUrlResponse)
async def dropbox_auth_url(request: Request) -> AuthUrlResponse:
    """Return the Dropbox OAuth URL for the user to visit."""
    settings = _get_settings(request)
    backup_cfg = settings.backup
    try:
        from atc.backup.dropbox_sync import DropboxSync

        sync = DropboxSync(
            app_key=getattr(backup_cfg, "dropbox_app_key", ""),
            app_secret=getattr(backup_cfg, "dropbox_app_secret", ""),
        )
        url = sync.get_auth_url()
        return AuthUrlResponse(url=url)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/dropbox/connect", response_model=ConnectResponse)
async def dropbox_connect(
    body: ConnectDropboxRequest,
    request: Request,
) -> ConnectResponse:
    """Exchange a Dropbox auth code for a refresh token and store it in config."""
    settings = _get_settings(request)
    backup_cfg = settings.backup
    try:
        from atc.backup.dropbox_sync import DropboxSync

        sync = DropboxSync(
            app_key=getattr(backup_cfg, "dropbox_app_key", ""),
            app_secret=getattr(backup_cfg, "dropbox_app_secret", ""),
        )
        token = sync.authenticate(body.code)
        # Persist the token to config.local.yaml via the settings router pattern
        # (the token is a secret — never written to config.yaml)
        backup_cfg.dropbox_refresh_token = token
        backup_cfg.dropbox_enabled = True
        logger.info("Dropbox connected and token stored in memory (restart to persist)")
        return ConnectResponse(ok=True, message="Dropbox connected successfully")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Google Drive OAuth
# ---------------------------------------------------------------------------


@router.get("/gdrive/auth-url", response_model=AuthUrlResponse)
async def gdrive_auth_url(request: Request) -> AuthUrlResponse:
    """Return the Google Drive OAuth URL for the user to visit."""
    settings = _get_settings(request)
    backup_cfg = settings.backup
    try:
        from atc.backup.gdrive_sync import GoogleDriveSync

        sync = GoogleDriveSync(
            client_id=getattr(backup_cfg, "gdrive_client_id", ""),
            client_secret=getattr(backup_cfg, "gdrive_client_secret", ""),
        )
        url = sync.get_auth_url()
        return AuthUrlResponse(url=url)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/gdrive/connect", response_model=ConnectResponse)
async def gdrive_connect(
    body: ConnectGDriveRequest,
    request: Request,
) -> ConnectResponse:
    """Exchange a Google Drive auth code for credentials and store them in config."""
    settings = _get_settings(request)
    backup_cfg = settings.backup
    try:
        from atc.backup.gdrive_sync import GoogleDriveSync

        sync = GoogleDriveSync(
            client_id=getattr(backup_cfg, "gdrive_client_id", ""),
            client_secret=getattr(backup_cfg, "gdrive_client_secret", ""),
        )
        creds = sync.authenticate(body.code)
        backup_cfg.gdrive_credentials = creds
        backup_cfg.gdrive_enabled = True
        logger.info("Google Drive connected and credentials stored in memory")
        return ConnectResponse(ok=True, message="Google Drive connected successfully")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=BackupStatusResponse)
async def backup_status(request: Request) -> BackupStatusResponse:
    """Return current auto-backup configuration and last backup time."""
    settings = _get_settings(request)
    backup_cfg = settings.backup
    db = request.app.state.db

    # Fetch last successful backup
    cursor = await db.execute(
        """SELECT created_at, size_bytes FROM backup_log
           WHERE status = 'success'
           ORDER BY created_at DESC LIMIT 1"""
    )
    last_row = await cursor.fetchone()

    return BackupStatusResponse(
        auto_backup_enabled=backup_cfg.auto_backup_enabled,
        auto_backup_interval_hours=backup_cfg.auto_backup_interval_hours,
        local_backup_dir=backup_cfg.local_backup_dir,
        keep_last_n=backup_cfg.keep_last_n,
        dropbox_enabled=backup_cfg.dropbox_enabled,
        dropbox_connected=backup_cfg.dropbox_refresh_token is not None,
        gdrive_enabled=backup_cfg.gdrive_enabled,
        gdrive_connected=backup_cfg.gdrive_credentials is not None,
        last_backup_at=last_row["created_at"] if last_row else None,
        last_backup_size_bytes=last_row["size_bytes"] if last_row else None,
    )

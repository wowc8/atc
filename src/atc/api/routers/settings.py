"""Settings router — export/import backup + agent provider config + sentry endpoints.

Routes:
  POST /api/settings/export/{project_id}  → export single project as zip
  POST /api/settings/export-all           → export all projects as zip
  POST /api/settings/import               → import project from zip
  POST /api/settings/import-all           → import all from full backup zip
  GET  /api/settings/agent-provider       → get current agent provider config
  PUT  /api/settings/agent-provider       → update agent provider config
  GET  /api/settings/providers            → list available providers
  GET  /api/settings/sentry              → get sentry status
  POST /api/settings/sentry/send-report  → manually send an error report
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from atc.backup.service import export_all, export_project, import_all, import_project
from atc.leader import leader as leader_ops
from atc.session import ace as ace_ops
from atc.state import db as db_ops

router = APIRouter()
logger = logging.getLogger(__name__)


async def _get_db(request: Request):  # noqa: ANN202
    return request.app.state.db


async def _capture_provider_replacement_handoff(
    request: Request,
    db: Any,
) -> dict[str, Any]:
    tower = request.app.state.tower_controller

    leader_rows_cursor = await db.execute(
        "SELECT project_id, goal, context FROM leaders WHERE session_id IS NOT NULL"
    )
    leader_rows = await leader_rows_cursor.fetchall()

    return {
        "tower": {
            "project_id": tower.current_project_id,
            "goal": tower.current_goal,
            "state": tower.state.value,
            "session_id": tower.current_session_id,
        },
        "leaders": [
            {
                "project_id": row[0],
                "goal": row[1],
                "context": row[2],
            }
            for row in leader_rows
        ],
    }


# ---------------------------------------------------------------------------
# Export endpoints
# ---------------------------------------------------------------------------


@router.post("/export/{project_id}")
async def export_project_endpoint(project_id: str, request: Request) -> Response:
    """Export a single project as a .atc-backup.zip."""
    db = await _get_db(request)
    try:
        zip_bytes = await export_project(db, project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{project_id}.atc-backup.zip"'},
    )


@router.post("/export-all")
async def export_all_endpoint(request: Request) -> Response:
    """Export all projects as a single .atc-backup.zip."""
    db = await _get_db(request)
    zip_bytes = await export_all(db)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="atc-full-backup.atc-backup.zip"'},
    )


# ---------------------------------------------------------------------------
# Import endpoints
# ---------------------------------------------------------------------------


class ImportRequest(BaseModel):
    """Import request with base64-encoded zip data."""

    data: str  # base64-encoded zip
    target_project_id: str | None = None


class ImportAllRequest(BaseModel):
    """Import-all request with base64-encoded zip data."""

    data: str  # base64-encoded zip


@router.post("/import")
async def import_project_endpoint(
    body: ImportRequest,
    request: Request,
) -> dict[str, object]:
    """Import a project from a base64-encoded zip.

    If target_project_id is given and exists, auto-backup + replace.
    Otherwise creates a new project.
    """
    db = await _get_db(request)
    try:
        raw = base64.b64decode(body.data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid base64 data: {e}") from None

    try:
        result = await import_project(
            db,
            raw,
            target_project_id=body.target_project_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None

    return result


@router.post("/import-all")
async def import_all_endpoint(
    body: ImportAllRequest,
    request: Request,
) -> dict[str, object]:
    """Import all projects from a base64-encoded full backup zip."""
    db = await _get_db(request)
    try:
        raw = base64.b64decode(body.data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid base64 data: {e}") from None

    try:
        result = await import_all(db, raw)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None

    return result


# ---------------------------------------------------------------------------
# Agent provider endpoints
# ---------------------------------------------------------------------------


class AgentProviderResponse(BaseModel):
    """Current agent provider configuration."""

    default: str
    opencode_url: str
    tmux_session: str
    claude_command: str
    codex_command: str


class AgentProviderUpdateRequest(BaseModel):
    """Update agent provider settings."""

    default: str | None = None
    opencode_url: str | None = None
    tmux_session: str | None = None
    claude_command: str | None = None
    codex_command: str | None = None


class ProviderInfo(BaseModel):
    """Info about an available provider."""

    name: str
    supports_streaming: bool
    supports_tool_use: bool
    context_window: int
    model: str


@router.get("/agent-provider")
async def get_agent_provider(request: Request) -> AgentProviderResponse:
    """Return the current agent provider configuration."""
    settings = request.app.state.settings
    cfg = settings.agent_provider
    return AgentProviderResponse(
        default=cfg.default,
        opencode_url=cfg.opencode_url,
        tmux_session=cfg.tmux_session,
        claude_command=cfg.claude_command,
        codex_command=cfg.codex_command,
    )


@router.put("/agent-provider")
async def update_agent_provider(
    body: AgentProviderUpdateRequest,
    request: Request,
) -> AgentProviderResponse:
    """Update agent provider configuration for the live app instance."""
    from atc.providers.registry import list_provider_runtimes
    from atc.tower.controller import TowerState

    settings = request.app.state.settings
    cfg = settings.agent_provider
    db = await _get_db(request)
    event_bus = request.app.state.event_bus
    tower = request.app.state.tower_controller

    old_default = cfg.default

    if body.default is not None:
        available = list_provider_runtimes()
        if body.default not in available:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown provider {body.default!r}. Available: {', '.join(available)}",
            )
        cfg.default = body.default
    if body.opencode_url is not None:
        cfg.opencode_url = body.opencode_url
    if body.tmux_session is not None:
        cfg.tmux_session = body.tmux_session
    if body.claude_command is not None:
        cfg.claude_command = body.claude_command
    if body.codex_command is not None:
        cfg.codex_command = body.codex_command

    provider_changed = body.default is not None and body.default != old_default
    if provider_changed:
        handoff = await _capture_provider_replacement_handoff(request, db)
        await db_ops.update_all_project_agent_providers(db, cfg.default)

        active_ace_sessions = await db_ops.list_sessions(db, session_type="ace")
        for session in active_ace_sessions:
            if session.provider != cfg.default and session.status not in ("error", "disconnected"):
                try:
                    await ace_ops.stop_ace(db, session.id, event_bus=event_bus)
                except Exception:
                    logger.exception("Failed to pause stale ace session %s during provider switch", session.id)

        for leader_info in handoff["leaders"]:
            project_id = leader_info["project_id"]
            try:
                await leader_ops.stop_leader(db, project_id, event_bus=event_bus)
            except Exception:
                logger.exception("Failed to stop leader for project %s during provider switch", project_id)

        tower_was_running = handoff["tower"]["session_id"] is not None and handoff["tower"]["state"] in ("managing", "planning", "idle")
        if handoff["tower"]["session_id"]:
            try:
                await tower.stop_session()
            except Exception:
                logger.exception("Failed to stop Tower during provider switch")
        if tower_was_running:
            try:
                restart_project_id = handoff["tower"]["project_id"]
                if restart_project_id is not None:
                    await tower.start_session(restart_project_id)
                else:
                    await tower.start_session()
            except Exception:
                logger.exception("Failed to restart Tower during provider switch")

    return AgentProviderResponse(
        default=cfg.default,
        opencode_url=cfg.opencode_url,
        tmux_session=cfg.tmux_session,
        claude_command=cfg.claude_command,
        codex_command=cfg.codex_command,
    )


@router.get("/providers")
async def list_available_providers() -> list[ProviderInfo]:
    """List all registered provider runtimes via the registry boundary."""
    from atc.providers.registry import list_provider_runtime_infos

    return [
        ProviderInfo(
            name=info.name,
            supports_streaming=info.supports_streaming,
            supports_tool_use=info.supports_tool_use,
            context_window=info.context_window,
            model=info.model,
        )
        for info in list_provider_runtime_infos()
    ]


# ---------------------------------------------------------------------------
# Sentry endpoints
# ---------------------------------------------------------------------------


class SentryStatusResponse(BaseModel):
    """Current Sentry configuration status."""

    enabled: bool
    initialized: bool
    environment: str


class SendReportRequest(BaseModel):
    """User-initiated error report."""

    message: str
    context: dict[str, object] | None = None


class SendReportResponse(BaseModel):
    """Result of sending an error report."""

    sent: bool
    event_id: str | None = None


@router.get("/sentry")
async def get_sentry_status(request: Request) -> SentryStatusResponse:
    """Return the current Sentry status."""
    settings = request.app.state.settings
    cfg = settings.sentry

    initialized = False
    try:
        import sentry_sdk

        initialized = sentry_sdk.is_initialized()
    except ImportError:
        pass

    return SentryStatusResponse(
        enabled=cfg.enabled,
        initialized=initialized,
        environment=cfg.environment,
    )


@router.post("/sentry/send-report")
async def send_sentry_report(
    body: SendReportRequest,
    request: Request,
) -> SendReportResponse:
    """Manually send a user-initiated error report to Sentry."""
    from atc.core.sentry import capture_message

    extra = dict(body.context) if body.context else {}
    extra["source"] = "user_report"

    event_id = capture_message(body.message, level="error", **extra)
    return SendReportResponse(sent=event_id is not None, event_id=event_id)


# ---------------------------------------------------------------------------
# Resource limits settings
# ---------------------------------------------------------------------------

class ResourceLimitsResponse(BaseModel):
    max_concurrent_aces: int
    cpu_throttle_threshold: float
    ram_throttle_threshold: float
    cpu_pause_threshold: float
    ram_pause_threshold: float


class ResourceLimitsUpdate(BaseModel):
    max_concurrent_aces: int | None = None
    cpu_throttle_threshold: float | None = None
    ram_throttle_threshold: float | None = None
    cpu_pause_threshold: float | None = None
    ram_pause_threshold: float | None = None


@router.get("/resource-limits", response_model=ResourceLimitsResponse)
async def get_resource_limits(request: Request) -> ResourceLimitsResponse:
    """Get current resource limit settings."""
    settings = request.app.state.settings
    cfg = settings.resource_monitor
    return ResourceLimitsResponse(
        max_concurrent_aces=cfg.max_concurrent_aces,
        cpu_throttle_threshold=cfg.cpu_throttle_threshold,
        ram_throttle_threshold=cfg.ram_throttle_threshold,
        cpu_pause_threshold=cfg.cpu_pause_threshold,
        ram_pause_threshold=cfg.ram_pause_threshold,
    )


@router.put("/resource-limits", response_model=ResourceLimitsResponse)
async def update_resource_limits(
    body: ResourceLimitsUpdate,
    request: Request,
) -> ResourceLimitsResponse:
    """Update resource limit settings (persisted in memory until restart)."""
    settings = request.app.state.settings
    cfg = settings.resource_monitor
    if body.max_concurrent_aces is not None:
        cfg.max_concurrent_aces = max(1, min(body.max_concurrent_aces, 20))
    if body.cpu_throttle_threshold is not None:
        cfg.cpu_throttle_threshold = body.cpu_throttle_threshold
    if body.ram_throttle_threshold is not None:
        cfg.ram_throttle_threshold = body.ram_throttle_threshold
    if body.cpu_pause_threshold is not None:
        cfg.cpu_pause_threshold = body.cpu_pause_threshold
    if body.ram_pause_threshold is not None:
        cfg.ram_pause_threshold = body.ram_pause_threshold
    return ResourceLimitsResponse(
        max_concurrent_aces=cfg.max_concurrent_aces,
        cpu_throttle_threshold=cfg.cpu_throttle_threshold,
        ram_throttle_threshold=cfg.ram_throttle_threshold,
        cpu_pause_threshold=cfg.cpu_pause_threshold,
        ram_pause_threshold=cfg.ram_pause_threshold,
    )

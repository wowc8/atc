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

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from atc.backup.service import export_all, export_project, import_all, import_project

router = APIRouter()
logger = logging.getLogger(__name__)


async def _get_db(request: Request):  # noqa: ANN202
    return request.app.state.db


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


class AgentProviderUpdateRequest(BaseModel):
    """Update agent provider settings."""

    default: str | None = None
    opencode_url: str | None = None
    tmux_session: str | None = None
    claude_command: str | None = None


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
    )


@router.put("/agent-provider")
async def update_agent_provider(
    body: AgentProviderUpdateRequest,
    request: Request,
) -> AgentProviderResponse:
    """Update agent provider configuration (runtime only, not persisted to file)."""
    from atc.agents.factory import list_providers

    settings = request.app.state.settings
    cfg = settings.agent_provider

    if body.default is not None:
        available = list_providers()
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

    return AgentProviderResponse(
        default=cfg.default,
        opencode_url=cfg.opencode_url,
        tmux_session=cfg.tmux_session,
        claude_command=cfg.claude_command,
    )


@router.get("/providers")
async def list_available_providers() -> list[ProviderInfo]:
    """List all registered agent providers with their capabilities."""
    from atc.agents.factory import create_provider, list_providers

    result: list[ProviderInfo] = []
    for name in list_providers():
        try:
            provider = create_provider(name)
            caps = provider.get_capabilities()
            result.append(
                ProviderInfo(
                    name=name,
                    supports_streaming=caps.supports_streaming,
                    supports_tool_use=caps.supports_tool_use,
                    context_window=caps.context_window,
                    model=caps.model,
                )
            )
        except Exception:
            logger.warning("Failed to instantiate provider %s for capabilities", name)
            result.append(
                ProviderInfo(
                    name=name,
                    supports_streaming=False,
                    supports_tool_use=False,
                    context_window=0,
                    model="",
                )
            )
    return result


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

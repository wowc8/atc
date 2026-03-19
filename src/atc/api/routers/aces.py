"""Ace session management REST endpoints.

Routes:
  GET    /api/projects/{project_id}/aces       → list ace sessions
  POST   /api/projects/{project_id}/aces       → spawn new ace
  POST   /api/aces/{session_id}/start          → start session
  POST   /api/aces/{session_id}/stop           → stop session
  PATCH  /api/aces/{session_id}/status         → update session status (from hooks)
  POST   /api/aces/{session_id}/message        → send message to ace
  POST   /api/aces/{session_id}/notify         → receive notification from hooks
  DELETE /api/aces/{session_id}                → delete session
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from atc.agents.factory import get_launch_command
from atc.core.errors import CreationFailedError, SessionNotFoundError, SessionStaleError
from atc.session import ace as ace_ops
from atc.session.state_machine import InvalidTransitionError
from atc.state import db as db_ops

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateAceRequest(BaseModel):
    name: str
    task_id: str | None = None
    host: str | None = None
    task_title: str | None = None
    task_description: str | None = None


class StartAceRequest(BaseModel):
    instruction: str | None = None


class StatusUpdateRequest(BaseModel):
    status: str


class MessageRequest(BaseModel):
    message: str


class NotifyRequest(BaseModel):
    message: str


class SessionResponse(BaseModel):
    id: str
    project_id: str
    session_type: str
    name: str
    status: str
    task_id: str | None = None
    host: str | None = None
    tmux_session: str | None = None
    tmux_pane: str | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_db(request: Request):  # noqa: ANN202
    """Get database connection from app state."""
    return request.app.state.db


async def _get_event_bus(request: Request):  # noqa: ANN202
    """Get event bus from app state (may be None during early startup)."""
    return getattr(request.app.state, "event_bus", None)


def _session_to_response(s: Any) -> SessionResponse:
    return SessionResponse(
        id=s.id,
        project_id=s.project_id,
        session_type=s.session_type,
        name=s.name,
        status=s.status,
        task_id=s.task_id,
        host=s.host,
        tmux_session=s.tmux_session,
        tmux_pane=s.tmux_pane,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/aces", response_model=list[SessionResponse])
async def list_aces(project_id: str, request: Request) -> list[SessionResponse]:
    db = await _get_db(request)
    sessions = await db_ops.list_sessions(db, project_id=project_id, session_type="ace")
    return [_session_to_response(s) for s in sessions]


@router.post("/projects/{project_id}/aces", response_model=SessionResponse, status_code=201)
async def create_ace(project_id: str, body: CreateAceRequest, request: Request) -> SessionResponse:
    db = await _get_db(request)
    event_bus = await _get_event_bus(request)

    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    launch_cmd = get_launch_command(project.agent_provider)

    try:
        session_id = await ace_ops.create_ace(
            db,
            project_id,
            body.name,
            task_id=body.task_id,
            host=body.host,
            event_bus=event_bus,
            working_dir=project.repo_path,
            launch_command=launch_cmd,
            # Pass deploy info so create_ace can deploy with the real session_id
            deploy_spec_kwargs={
                "project_name": project.name,
                "task_title": body.task_title or body.name,
                "task_description": body.task_description,
                "repo_path": project.repo_path,
                "github_repo": project.github_repo,
            },
        )
    except RuntimeError as exc:
        raise CreationFailedError(str(exc)) from None
    session = await db_ops.get_session(db, session_id)
    if session is None:
        raise CreationFailedError(f"Session creation failed for project {project_id}")
    return _session_to_response(session)


@router.post("/aces/{session_id}/start")
async def start_ace(session_id: str, body: StartAceRequest, request: Request) -> dict[str, str]:
    db = await _get_db(request)
    event_bus = await _get_event_bus(request)
    try:
        await ace_ops.start_ace(db, session_id, instruction=body.instruction, event_bus=event_bus)
    except ValueError as e:
        raise SessionNotFoundError(str(e)) from None
    except InvalidTransitionError as e:
        raise SessionStaleError(str(e)) from None
    return {"status": "started"}


@router.post("/aces/{session_id}/stop")
async def stop_ace(session_id: str, request: Request) -> dict[str, str]:
    db = await _get_db(request)
    event_bus = await _get_event_bus(request)
    try:
        await ace_ops.stop_ace(db, session_id, event_bus=event_bus)
    except ValueError as e:
        raise SessionNotFoundError(str(e)) from None
    except InvalidTransitionError as e:
        raise SessionStaleError(str(e)) from None
    return {"status": "stopped"}


@router.patch("/aces/{session_id}/status")
async def update_ace_status(
    session_id: str,
    body: StatusUpdateRequest,
    request: Request,
) -> dict[str, str]:
    """Update session status — called by PostToolUse and Stop hooks."""
    from atc.session.state_machine import SessionStatus, transition

    db = await _get_db(request)
    event_bus = await _get_event_bus(request)

    session = await db_ops.get_session(db, session_id)
    if session is None:
        raise SessionNotFoundError(f"Session {session_id} not found")

    try:
        target = SessionStatus(body.status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status: {body.status}") from None

    current = SessionStatus(session.status)
    if current == target:
        return {"status": target.value}

    try:
        await transition(session.id, current, target, event_bus)
        await db_ops.update_session_status(db, session.id, target.value)
    except InvalidTransitionError as exc:
        raise SessionStaleError(str(exc)) from None

    return {"status": target.value}


@router.post("/aces/{session_id}/notify")
async def notify_ace(
    session_id: str,
    body: NotifyRequest,
    request: Request,
) -> dict[str, str]:
    """Receive a notification from an agent's Notification hook."""
    import logging
    import uuid
    from datetime import UTC, datetime

    db = await _get_db(request)

    session = await db_ops.get_session(db, session_id)
    if session is None:
        raise SessionNotFoundError(f"Session {session_id} not found")

    now = datetime.now(UTC).isoformat()
    await db.execute(
        """INSERT INTO notifications (id, project_id, level, message, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), session.project_id, "info", body.message, now),
    )
    await db.commit()

    # Broadcast to WebSocket clients
    ws_hub = getattr(request.app.state, "ws_hub", None)
    if ws_hub is not None:
        await ws_hub.broadcast(
            "notifications",
            {
                "session_id": session_id,
                "message": body.message,
            },
        )

    logger = logging.getLogger(__name__)
    logger.info("Notification from %s: %s", session_id, body.message)

    return {"status": "received"}


@router.post("/aces/{session_id}/message")
async def message_ace(
    session_id: str,
    body: MessageRequest,
    request: Request,
) -> dict[str, str]:
    db = await _get_db(request)
    event_bus = await _get_event_bus(request)

    session = await db_ops.get_session(db, session_id)
    if session is None:
        raise SessionNotFoundError(f"Session {session_id} not found")

    if session.tmux_pane is None:
        raise SessionStaleError("Session has no tmux pane")

    from atc.session.ace import _send_keys
    from atc.session.state_machine import SessionStatus, transition

    current = SessionStatus(session.status)
    if current in (SessionStatus.IDLE, SessionStatus.WAITING):
        try:
            await transition(session.id, current, SessionStatus.WORKING, event_bus)
            await db_ops.update_session_status(db, session.id, SessionStatus.WORKING.value)
        except InvalidTransitionError:
            pass

    await _send_keys(session.tmux_pane, body.message)
    return {"status": "sent"}


@router.delete("/aces/{session_id}", status_code=204)
async def delete_ace(session_id: str, request: Request) -> None:
    db = await _get_db(request)
    event_bus = await _get_event_bus(request)
    try:
        await ace_ops.destroy_ace(db, session_id, event_bus=event_bus)
    except ValueError as e:
        raise SessionNotFoundError(str(e)) from None

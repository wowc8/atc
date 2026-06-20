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

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from atc.api.delivery import delivery_response
from atc.core.errors import CreationFailedError, SessionNotFoundError, SessionStaleError
from atc.runtime.health import ace_health, apply_recovery_plan, build_recovery_plan
from atc.session import ace as ace_ops
from atc.session.state_machine import InvalidTransitionError
from atc.state import db as db_ops

router = APIRouter()
logger = logging.getLogger(__name__)


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


class RecoveryRequest(BaseModel):
    dry_run: bool = True
    policy: str = "inspect_first"


class AceActiveReportRequest(BaseModel):
    assignment_id: str | None = None
    assignment_accepted: bool = True
    message: str | None = None


class AceArtifactReportRequest(BaseModel):
    assignment_id: str | None = None
    artifact_path: str
    artifact_kind: str = "build_output"
    ready: bool = True


async def _require_project_ace(db, project_id: str, session_id: str):
    session = await db_ops.get_session(db, session_id)
    if session is None or session.project_id != project_id or session.session_type != "ace":
        raise HTTPException(status_code=404, detail="Ace session not found")
    return session


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

    try:
        session_id = await ace_ops.create_ace(
            db,
            project_id,
            body.name,
            task_id=body.task_id,
            host=body.host,
            event_bus=event_bus,
            working_dir=project.repo_path,
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


@router.get("/projects/{project_id}/aces/{session_id}/health")
async def get_ace_health(
    project_id: str,
    session_id: str,
    request: Request,
) -> dict[str, object]:
    """Return provider-neutral Ace runtime/dispatch health."""
    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await _require_project_ace(db, project_id, session_id)
    health = await ace_health(db, project_id, session_id)
    return health.as_dict()


@router.post("/projects/{project_id}/aces/{session_id}/report-active")
async def report_ace_active(
    project_id: str,
    session_id: str,
    body: AceActiveReportRequest,
    request: Request,
) -> dict[str, object]:
    """Record Ace-side assignment acceptance before Leader treats work as active."""
    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await _require_project_ace(db, project_id, session_id)
    assignments = await db_ops.list_task_assignments(db, ace_session_id=session_id)
    if body.assignment_id:
        assignment = next((a for a in assignments if a.assignment_id == body.assignment_id), None)
    else:
        assignment = next((a for a in assignments if a.status in {"assigned", "working"}), None)
    if assignment is None:
        raise HTTPException(status_code=404, detail="Active Ace assignment not found")

    report = await db_ops.report_ace_assignment_active(
        db,
        assignment.assignment_id,
        accepted=body.assignment_accepted,
        message=body.message,
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Ace assignment not found")
    if body.assignment_accepted:
        await db_ops.update_session_status(db, session_id, "working")
    acceptance_state = "assignment_accepted" if report.assignment_accepted else "active_unaccepted"
    dispatch = {
        "assignment_id": report.assignment_id,
        "task_graph_id": report.task_graph_id,
        "assignment_status": report.status,
        "dispatch_delivery_state": report.dispatch_delivery_state,
        "dispatch_verified": report.dispatch_verified,
        "assignment_acceptance_state": acceptance_state,
        "ace_reported_active": report.ace_reported_active,
        "assignment_accepted": report.assignment_accepted,
        "assignment_accepted_at": report.assignment_accepted_at,
        "acceptance_message": report.acceptance_message,
        "first_work_observed_at": report.last_activity_at,
        "assigned_task_id": report.assigned_task_id,
        "blocker_reason": report.blocker_reason,
    }
    return {
        "status": "accepted",
        "project_id": project_id,
        "ace_id": session_id,
        "assignment_id": report.assignment_id,
        "assignment_accepted": report.assignment_accepted,
        "ace_reported_active": report.ace_reported_active,
        "assignment_accepted_at": report.assignment_accepted_at,
        "assignment_acceptance_state": dispatch.get("assignment_acceptance_state"),
        "ace_dispatch": dispatch,
    }


@router.post("/projects/{project_id}/aces/{session_id}/report-artifact")
async def report_ace_artifact(
    project_id: str,
    session_id: str,
    body: AceArtifactReportRequest,
    request: Request,
) -> dict[str, object]:
    """Record a canonical artifact path produced by an Ace assignment."""
    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await _require_project_ace(db, project_id, session_id)
    assignments = await db_ops.list_task_assignments(db, ace_session_id=session_id)
    if body.assignment_id:
        assignment = next((a for a in assignments if a.assignment_id == body.assignment_id), None)
    else:
        assignment = next(
            (a for a in assignments if a.status in {"assigned", "working", "done"}),
            None,
        )
    if assignment is None:
        raise HTTPException(status_code=404, detail="Ace assignment not found")
    report = await db_ops.report_task_assignment_artifact(
        db,
        assignment.assignment_id,
        artifact_path=body.artifact_path,
        artifact_kind=body.artifact_kind,
        ready=body.ready,
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Ace assignment not found")
    return {
        "status": "artifact_ready" if report.artifact_ready else "artifact_reported",
        "project_id": project_id,
        "ace_id": session_id,
        "assignment_id": report.assignment_id,
        "task_graph_id": report.task_graph_id,
        "artifact_path": report.artifact_path,
        "artifact_kind": report.artifact_kind,
        "artifact_ready": report.artifact_ready,
        "artifact_reported_at": report.artifact_reported_at,
    }


@router.post("/projects/{project_id}/aces/{session_id}/recover")
async def recover_ace(
    project_id: str,
    session_id: str,
    body: RecoveryRequest,
    request: Request,
) -> dict[str, object]:
    """Inspect-first Ace recovery plan; mutation is policy-gated."""

    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await _require_project_ace(db, project_id, session_id)
    health = await ace_health(db, project_id, session_id)
    plan = (
        build_recovery_plan(health, mode="dry_run", policy=body.policy)
        if body.dry_run
        else await apply_recovery_plan(db, health, policy=body.policy)
    )
    if plan.refused_reason:
        raise HTTPException(status_code=409, detail=plan.as_dict())
    return plan.as_dict()


@router.post("/aces/{session_id}/start")
async def start_ace(session_id: str, body: StartAceRequest, request: Request) -> dict[str, object]:
    db = await _get_db(request)
    event_bus = await _get_event_bus(request)
    try:
        result = await ace_ops.start_ace(
            db, session_id, instruction=body.instruction, event_bus=event_bus
        )
    except ValueError as e:
        raise SessionNotFoundError(str(e)) from None
    except InvalidTransitionError as e:
        raise SessionStaleError(str(e)) from None
    except RuntimeError as e:
        raise SessionStaleError(str(e)) from None
    return delivery_response(
        result,
        fallback_state="queued" if body.instruction is None else "submitted",
        message=(
            "Ace session queued/started; no instruction delivery was requested or verified"
            if body.instruction is None
            else "Ace instruction submitted; provider acknowledgement is not verified"
        ),
        session_id=session_id,
        recovery="inspect Ace runtime/session status for delivery confirmation"
        if body.instruction is not None
        else None,
    )


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
        if target in {SessionStatus.WORKING, SessionStatus.WAITING}:
            logger.info(
                "Ignoring stale hook status for ace %s: %s -> %s (%s)",
                session.id,
                current.value,
                target.value,
                exc,
            )
            return {"status": current.value}
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
) -> dict[str, object]:
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
    return delivery_response(
        None,
        fallback_state="submitted",
        message="Message submitted to Ace terminal; provider acknowledgement is not verified",
        session_id=session_id,
        project_id=session.project_id,
        provider=session.provider,
        recovery="inspect Ace runtime/session status and task assignment state for confirmation",
    )


@router.delete("/aces/{session_id}", status_code=204)
async def delete_ace(session_id: str, request: Request) -> None:
    db = await _get_db(request)
    event_bus = await _get_event_bus(request)
    try:
        await ace_ops.destroy_ace(db, session_id, event_bus=event_bus)
    except ValueError as e:
        raise SessionNotFoundError(str(e)) from None

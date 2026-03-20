"""Context entries CRUD REST endpoints.

Routes:
  GET    /api/context                        → list global context entries
  POST   /api/context                        → create global context entry
  GET    /api/projects/{id}/context           → list project context entries
  POST   /api/projects/{id}/context           → create project context entry
  GET    /api/sessions/{id}/context           → list session-scoped entries
  POST   /api/sessions/{id}/context           → create session-scoped entry
  GET    /api/context/{entry_id}              → get single entry (any scope)
  PUT    /api/context/{entry_id}              → update entry
  DELETE /api/context/{entry_id}              → delete entry

Query params (on list endpoints): ?scope=, ?restricted=, ?key=
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from atc.state import db as db_ops

logger = logging.getLogger(__name__)

router = APIRouter()

_VALID_SCOPES = {"global", "project", "tower", "leader", "ace"}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ContextEntryResponse(BaseModel):
    id: str
    scope: str
    project_id: str | None = None
    session_id: str | None = None
    key: str
    entry_type: str
    value: str
    restricted: bool = False
    position: int = 0
    updated_by: str = ""
    created_at: str = ""
    updated_at: str = ""


class CreateContextEntryRequest(BaseModel):
    key: str
    entry_type: str = "text"
    value: str
    restricted: bool = False
    position: int = 0
    updated_by: str = ""


class CreateSessionContextEntryRequest(BaseModel):
    scope: str  # tower|leader|ace
    key: str
    entry_type: str = "text"
    value: str
    restricted: bool = False
    position: int = 0
    updated_by: str = ""


class UpdateContextEntryRequest(BaseModel):
    value: str | None = None
    entry_type: str | None = None
    position: int | None = None
    restricted: bool | None = None
    updated_by: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_db(request: Request):  # noqa: ANN202
    return request.app.state.db


def _entry_to_response(entry) -> ContextEntryResponse:  # noqa: ANN001
    return ContextEntryResponse(**entry.__dict__)


# ---------------------------------------------------------------------------
# Global context endpoints
# ---------------------------------------------------------------------------


@router.get("/context", response_model=list[ContextEntryResponse])
async def list_global_context(
    request: Request,
    restricted: bool | None = Query(None),
    key: str | None = Query(None),
) -> list[ContextEntryResponse]:
    """List all global-scoped context entries."""
    db = await _get_db(request)
    entries = await db_ops.list_context_entries_by_scope(db, "global")
    if restricted is not None:
        entries = [e for e in entries if e.restricted is restricted]
    if key is not None:
        entries = [e for e in entries if e.key == key]
    return [_entry_to_response(e) for e in entries]


@router.post("/context", response_model=ContextEntryResponse, status_code=201)
async def create_global_context(
    body: CreateContextEntryRequest,
    request: Request,
) -> ContextEntryResponse:
    """Create a global-scoped context entry."""
    db = await _get_db(request)
    try:
        entry = await db_ops.create_context_entry(
            db,
            "global",
            body.key,
            body.entry_type,
            body.value,
            restricted=body.restricted,
            position=body.position,
            updated_by=body.updated_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception:
        logger.exception("Failed to create global context entry")
        raise HTTPException(status_code=409, detail="Duplicate key") from None
    return _entry_to_response(entry)


# ---------------------------------------------------------------------------
# Project context endpoints
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/context", response_model=list[ContextEntryResponse])
async def list_project_context(
    project_id: str,
    request: Request,
    scope: str | None = Query(None),
    restricted: bool | None = Query(None),
    key: str | None = Query(None),
) -> list[ContextEntryResponse]:
    """List context entries for a project. Defaults to scope='project'."""
    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if scope is not None:
        if scope not in _VALID_SCOPES:
            raise HTTPException(status_code=422, detail=f"Invalid scope: {scope}")
        entries = await db_ops.list_context_entries_by_scope(
            db, scope, project_id=project_id,
        )
    else:
        entries = await db_ops.list_context_entries_by_scope(
            db, "project", project_id=project_id,
        )

    if restricted is not None:
        entries = [e for e in entries if e.restricted is restricted]
    if key is not None:
        entries = [e for e in entries if e.key == key]
    return [_entry_to_response(e) for e in entries]


@router.post(
    "/projects/{project_id}/context",
    response_model=ContextEntryResponse,
    status_code=201,
)
async def create_project_context(
    project_id: str,
    body: CreateContextEntryRequest,
    request: Request,
) -> ContextEntryResponse:
    """Create a project-scoped context entry."""
    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        entry = await db_ops.create_context_entry(
            db,
            "project",
            body.key,
            body.entry_type,
            body.value,
            project_id=project_id,
            restricted=body.restricted,
            position=body.position,
            updated_by=body.updated_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception:
        logger.exception("Failed to create project context entry")
        raise HTTPException(status_code=409, detail="Duplicate key") from None
    return _entry_to_response(entry)


# ---------------------------------------------------------------------------
# Session context endpoints (tower / leader / ace)
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/context", response_model=list[ContextEntryResponse])
async def list_session_context(
    session_id: str,
    request: Request,
    scope: str | None = Query(None),
    restricted: bool | None = Query(None),
    key: str | None = Query(None),
) -> list[ContextEntryResponse]:
    """List context entries for a session (tower/leader/ace)."""
    db = await _get_db(request)
    session = await db_ops.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Map session_type to context scope
    scope_map = {"tower": "tower", "manager": "leader", "ace": "ace"}
    effective_scope = scope or scope_map.get(session.session_type, session.session_type)

    if effective_scope not in _VALID_SCOPES:
        raise HTTPException(status_code=422, detail=f"Invalid scope: {effective_scope}")

    entries = await db_ops.list_context_entries_by_scope(
        db, effective_scope, session_id=session_id,
    )
    if restricted is not None:
        entries = [e for e in entries if e.restricted is restricted]
    if key is not None:
        entries = [e for e in entries if e.key == key]
    return [_entry_to_response(e) for e in entries]


@router.post(
    "/sessions/{session_id}/context",
    response_model=ContextEntryResponse,
    status_code=201,
)
async def create_session_context(
    session_id: str,
    body: CreateSessionContextEntryRequest,
    request: Request,
) -> ContextEntryResponse:
    """Create a session-scoped context entry (tower/leader/ace)."""
    db = await _get_db(request)
    session = await db_ops.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if body.scope not in ("tower", "leader", "ace"):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid session scope: {body.scope}. Must be tower, leader, or ace.",
        )

    try:
        entry = await db_ops.create_context_entry(
            db,
            body.scope,
            body.key,
            body.entry_type,
            body.value,
            session_id=session_id,
            restricted=body.restricted,
            position=body.position,
            updated_by=body.updated_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception:
        logger.exception("Failed to create session context entry")
        raise HTTPException(status_code=409, detail="Duplicate key") from None
    return _entry_to_response(entry)


# ---------------------------------------------------------------------------
# Single-entry endpoints (any scope)
# ---------------------------------------------------------------------------


@router.get("/context/{entry_id}", response_model=ContextEntryResponse)
async def get_context_entry(
    entry_id: str,
    request: Request,
) -> ContextEntryResponse:
    """Get a single context entry by ID."""
    db = await _get_db(request)
    entry = await db_ops.get_context_entry(db, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Context entry not found")
    return _entry_to_response(entry)


@router.put("/context/{entry_id}", response_model=ContextEntryResponse)
async def update_context_entry(
    entry_id: str,
    body: UpdateContextEntryRequest,
    request: Request,
) -> ContextEntryResponse:
    """Update a context entry."""
    db = await _get_db(request)
    updated = await db_ops.update_context_entry(
        db,
        entry_id,
        value=body.value,
        entry_type=body.entry_type,
        position=body.position,
        restricted=body.restricted,
        updated_by=body.updated_by,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Context entry not found")
    return _entry_to_response(updated)


@router.delete("/context/{entry_id}", status_code=204)
async def delete_context_entry(
    entry_id: str,
    request: Request,
) -> None:
    """Delete a context entry."""
    db = await _get_db(request)
    deleted = await db_ops.delete_context_entry(db, entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Context entry not found")

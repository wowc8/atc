"""Failure log REST endpoints.

Routes:
  GET    /api/failure-logs                → list/filter failure logs
  GET    /api/failure-logs/{log_id}       → get single failure log
  PATCH  /api/failure-logs/{log_id}/resolve → mark as resolved
  GET    /api/failure-logs/unresolved-count → count of unresolved failures
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from atc.core.failure_log import list_failures, resolve_failure

router = APIRouter()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class FailureLogResponse(BaseModel):
    id: str
    level: str
    category: str
    message: str
    context: dict[str, Any] | None = None
    project_id: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    stack_trace: str | None = None
    resolved: bool
    created_at: str


class UnresolvedCountResponse(BaseModel):
    count: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_db(request: Request) -> Any:
    return request.app.state.db


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/failure-logs/unresolved-count",
    response_model=UnresolvedCountResponse,
)
async def get_unresolved_count(request: Request) -> UnresolvedCountResponse:
    """Return count of unresolved failure logs."""
    db = await _get_db(request)
    failures = await list_failures(db, resolved=False, limit=10000)
    return UnresolvedCountResponse(count=len(failures))


@router.get(
    "/failure-logs",
    response_model=list[FailureLogResponse],
)
async def get_failure_logs(
    request: Request,
    level: str | None = Query(None, description="Filter by level"),
    category: str | None = Query(None, description="Filter by category"),
    project_id: str | None = Query(None, description="Filter by project"),
    resolved: bool | None = Query(None, description="Filter by resolved status"),
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
) -> list[FailureLogResponse]:
    """List failure logs with optional filters."""
    db = await _get_db(request)
    failures = await list_failures(
        db,
        level=level,
        category=category,
        project_id=project_id,
        resolved=resolved,
        limit=limit,
    )
    return [FailureLogResponse(**f) for f in failures]


@router.get(
    "/failure-logs/{log_id}",
    response_model=FailureLogResponse,
)
async def get_failure_log(log_id: str, request: Request) -> FailureLogResponse:
    """Get a single failure log entry."""
    db = await _get_db(request)
    failures = await list_failures(db, limit=10000)
    for f in failures:
        if f["id"] == log_id:
            return FailureLogResponse(**f)
    raise HTTPException(status_code=404, detail=f"Failure log {log_id} not found")


@router.patch(
    "/failure-logs/{log_id}/resolve",
    response_model=FailureLogResponse,
)
async def resolve_failure_log(log_id: str, request: Request) -> FailureLogResponse:
    """Mark a failure log entry as resolved."""
    db = await _get_db(request)

    # Verify exists
    failures = await list_failures(db, limit=10000)
    entry = None
    for f in failures:
        if f["id"] == log_id:
            entry = f
            break
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Failure log {log_id} not found")

    await resolve_failure(db, log_id)

    # Broadcast update via WebSocket
    ws_hub = getattr(request.app.state, "ws_hub", None)
    if ws_hub:
        await ws_hub.broadcast("failure_logs", {"resolved": log_id})

    entry["resolved"] = True
    return FailureLogResponse(**entry)

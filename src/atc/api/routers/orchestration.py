from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from atc.orchestration.errors import OrchestrationException
from atc.orchestration.models import (
    ListSessionsRequest,
    OperationAcceptedResponse,
    SessionSummary,
    SpawnLeaderRequest,
)
from atc.orchestration.service import OrchestrationService

router = APIRouter()


async def _get_service(request: Request) -> OrchestrationService:
    db = request.app.state.db
    tower_controller = getattr(request.app.state, "tower_controller", None)
    return OrchestrationService(db, tower_controller=tower_controller)


@router.post("/leaders", response_model=OperationAcceptedResponse, status_code=202)
async def spawn_leader(body: SpawnLeaderRequest, request: Request) -> OperationAcceptedResponse:
    service = await _get_service(request)
    try:
        return await service.spawn_leader(body)
    except OrchestrationException as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from None


@router.get("/sessions/{session_id}", response_model=SessionSummary)
async def get_session(session_id: str, request: Request) -> SessionSummary:
    service = await _get_service(request)
    try:
        return await service.get_session(session_id)
    except OrchestrationException as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from None


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions(
    request: Request,
    project_id: str | None = None,
    role: str | None = None,
    active_only: bool = False,
    limit: int | None = None,
) -> list[SessionSummary]:
    service = await _get_service(request)
    try:
        model = ListSessionsRequest(
            project_id=project_id,
            role=role,
            active_only=active_only,
            limit=limit,
        )
        return await service.list_sessions(model)
    except OrchestrationException as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from None

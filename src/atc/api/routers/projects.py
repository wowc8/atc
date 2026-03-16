"""Project CRUD + Leader lifecycle REST endpoints.

Routes:
  GET    /api/projects                          → list all projects
  POST   /api/projects                          → create project (auto-creates Leader)
  GET    /api/projects/{id}                     → project detail
  GET    /api/projects/{id}/manager             → Leader detail
  POST   /api/projects/{id}/leader/start        → start Leader session
  POST   /api/projects/{id}/leader/stop         → stop Leader session
  POST   /api/projects/{id}/leader/message      → send message to Leader
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from atc.leader import leader as leader_ops
from atc.state import db as db_ops

router = APIRouter()


class CreateProjectRequest(BaseModel):
    name: str
    description: str | None = None
    repo_path: str | None = None
    github_repo: str | None = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    status: str
    description: str | None = None
    repo_path: str | None = None
    github_repo: str | None = None
    created_at: str
    updated_at: str


class LeaderResponse(BaseModel):
    id: str
    project_id: str
    status: str
    session_id: str | None = None
    goal: str | None = None
    created_at: str
    updated_at: str


class LeaderStartRequest(BaseModel):
    goal: str | None = None


class LeaderMessageRequest(BaseModel):
    message: str


async def _get_db(request: Request):  # noqa: ANN202
    return request.app.state.db


async def _get_event_bus(request: Request):  # noqa: ANN202
    return getattr(request.app.state, "event_bus", None)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(request: Request) -> list[ProjectResponse]:
    db = await _get_db(request)
    projects = await db_ops.list_projects(db)
    return [ProjectResponse(**p.__dict__) for p in projects]


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(body: CreateProjectRequest, request: Request) -> ProjectResponse:
    db = await _get_db(request)
    project = await db_ops.create_project(
        db,
        body.name,
        description=body.description,
        repo_path=body.repo_path,
        github_repo=body.github_repo,
    )
    # Auto-create Leader for the project
    await db_ops.create_leader(db, project.id)
    return ProjectResponse(**project.__dict__)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, request: Request) -> ProjectResponse:
    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(**project.__dict__)


@router.get("/{project_id}/manager", response_model=LeaderResponse)
async def get_leader(project_id: str, request: Request) -> LeaderResponse:
    db = await _get_db(request)
    leader = await db_ops.get_leader_by_project(db, project_id)
    if leader is None:
        raise HTTPException(status_code=404, detail="No leader for this project")
    return LeaderResponse(
        id=leader.id,
        project_id=leader.project_id,
        status=leader.status,
        session_id=leader.session_id,
        goal=leader.goal,
        created_at=leader.created_at,
        updated_at=leader.updated_at,
    )


@router.post("/{project_id}/leader/start")
async def start_leader(
    project_id: str, body: LeaderStartRequest, request: Request
) -> dict[str, str]:
    db = await _get_db(request)
    event_bus = await _get_event_bus(request)
    session_id = await leader_ops.start_leader(
        db, project_id, goal=body.goal, event_bus=event_bus
    )
    return {"status": "started", "session_id": session_id}


@router.post("/{project_id}/leader/stop")
async def stop_leader(project_id: str, request: Request) -> dict[str, str]:
    db = await _get_db(request)
    event_bus = await _get_event_bus(request)
    await leader_ops.stop_leader(db, project_id, event_bus=event_bus)
    return {"status": "stopped"}


@router.post("/{project_id}/leader/message")
async def send_leader_message(
    project_id: str, body: LeaderMessageRequest, request: Request
) -> dict[str, str]:
    db = await _get_db(request)
    event_bus = await _get_event_bus(request)
    try:
        await leader_ops.send_leader_message(
            db, project_id, body.message, event_bus=event_bus
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return {"status": "sent"}

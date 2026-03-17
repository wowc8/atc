"""Task graph management REST endpoints.

Routes:
  GET    /api/projects/{project_id}/task-graphs         → list task graphs
  POST   /api/projects/{project_id}/task-graphs         → create task graph
  GET    /api/task-graphs/{task_graph_id}               → get task graph
  PATCH  /api/task-graphs/{task_graph_id}               → update task graph
  PATCH  /api/task-graphs/{task_graph_id}/status        → transition status
  DELETE /api/task-graphs/{task_graph_id}               → delete task graph
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from atc.state import db as db_ops

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateTaskGraphRequest(BaseModel):
    title: str
    description: str | None = None
    status: str = "todo"
    assigned_ace_id: str | None = None
    dependencies: list[str] | None = None


class UpdateTaskGraphRequest(BaseModel):
    model_config = {"extra": "forbid"}
    title: str | None = None
    description: str | None = None
    assigned_ace_id: str | None = None
    dependencies: list[str] | None = None


class StatusTransitionRequest(BaseModel):
    status: str


class TaskGraphResponse(BaseModel):
    id: str
    project_id: str
    title: str
    description: str | None = None
    status: str
    assigned_ace_id: str | None = None
    dependencies: list[str] | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_db(request: Request) -> Any:
    return request.app.state.db


def _to_response(tg: Any) -> TaskGraphResponse:
    return TaskGraphResponse(
        id=tg.id,
        project_id=tg.project_id,
        title=tg.title,
        description=tg.description,
        status=tg.status,
        assigned_ace_id=tg.assigned_ace_id,
        dependencies=tg.dependencies,
        created_at=tg.created_at,
        updated_at=tg.updated_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/task-graphs",
    response_model=list[TaskGraphResponse],
)
async def list_task_graphs(
    project_id: str, request: Request,
) -> list[TaskGraphResponse]:
    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    items = await db_ops.list_task_graphs(db, project_id=project_id)
    return [_to_response(tg) for tg in items]


@router.post(
    "/projects/{project_id}/task-graphs",
    response_model=TaskGraphResponse,
    status_code=201,
)
async def create_task_graph(
    project_id: str, body: CreateTaskGraphRequest, request: Request,
) -> TaskGraphResponse:
    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    try:
        tg = await db_ops.create_task_graph(
            db,
            project_id,
            body.title,
            description=body.description,
            status=body.status,
            assigned_ace_id=body.assigned_ace_id,
            dependencies=body.dependencies,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return _to_response(tg)


@router.get(
    "/task-graphs/{task_graph_id}",
    response_model=TaskGraphResponse,
)
async def get_task_graph(
    task_graph_id: str, request: Request,
) -> TaskGraphResponse:
    db = await _get_db(request)
    tg = await db_ops.get_task_graph(db, task_graph_id)
    if tg is None:
        raise HTTPException(
            status_code=404, detail=f"TaskGraph {task_graph_id} not found",
        )
    return _to_response(tg)


@router.patch(
    "/task-graphs/{task_graph_id}",
    response_model=TaskGraphResponse,
)
async def update_task_graph(
    task_graph_id: str, body: UpdateTaskGraphRequest, request: Request,
) -> TaskGraphResponse:
    db = await _get_db(request)

    kwargs: dict[str, Any] = {}
    raw = body.model_dump(exclude_unset=True)
    if "title" in raw:
        kwargs["title"] = raw["title"]
    if "description" in raw:
        kwargs["description"] = raw["description"]
    if "assigned_ace_id" in raw:
        kwargs["assigned_ace_id"] = raw["assigned_ace_id"]
    if "dependencies" in raw:
        kwargs["dependencies"] = raw["dependencies"]

    tg = await db_ops.update_task_graph(db, task_graph_id, **kwargs)
    if tg is None:
        raise HTTPException(
            status_code=404, detail=f"TaskGraph {task_graph_id} not found",
        )
    return _to_response(tg)


@router.patch(
    "/task-graphs/{task_graph_id}/status",
    response_model=TaskGraphResponse,
)
async def transition_task_graph_status(
    task_graph_id: str, body: StatusTransitionRequest, request: Request,
) -> TaskGraphResponse:
    db = await _get_db(request)
    try:
        tg = await db_ops.update_task_graph_status(db, task_graph_id, body.status)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    if tg is None:
        raise HTTPException(
            status_code=404, detail=f"TaskGraph {task_graph_id} not found",
        )
    return _to_response(tg)


@router.delete("/task-graphs/{task_graph_id}", status_code=204)
async def delete_task_graph(
    task_graph_id: str, request: Request,
) -> None:
    db = await _get_db(request)
    deleted = await db_ops.delete_task_graph(db, task_graph_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"TaskGraph {task_graph_id} not found",
        )

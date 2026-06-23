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
from pydantic import BaseModel, Field

from atc.orchestration.handoff import handoff_from_assignment
from atc.runtime.models import DeliveryState, RuntimeState
from atc.state import db as db_ops
from atc.state.transitions import LifecycleTransitionError

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


class RuntimeTruthSummary(BaseModel):
    """Provider-neutral runtime/delivery truth for a task graph row.

    ``task_state`` is the product task lifecycle. ``runtime_state`` and
    ``delivery_state`` describe what ATC has verified about the assigned Ace
    runtime/instruction delivery.
    """

    task_state: str
    runtime_state: str
    delivery_state: str
    assignment_status: str | None = None
    dispatch_verified: bool = False
    blocker_reason: str | None = None
    last_activity_at: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class TaskGraphResponse(BaseModel):
    id: str
    project_id: str
    title: str
    description: str | None = None
    status: str
    task_state: str
    runtime_state: str = RuntimeState.IDLE.value
    delivery_state: str = DeliveryState.NOT_STARTED.value
    assignment_status: str | None = None
    dispatch_verified: bool = False
    blocker_reason: str | None = None
    last_activity_at: str | None = None
    runtime_truth: RuntimeTruthSummary
    assigned_ace_id: str | None = None
    dependencies: list[str] | None = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_db(request: Request) -> Any:
    return request.app.state.db


def _transition_error_detail(exc: LifecycleTransitionError) -> dict[str, object]:
    """Return normalized lifecycle-transition error detail for API callers."""
    return exc.to_detail()


def _runtime_truth_for_task(tg: Any, assignment: Any | None = None) -> RuntimeTruthSummary:
    task_state = tg.status
    delivery_state = (
        assignment.dispatch_delivery_state
        if assignment is not None
        else DeliveryState.NOT_STARTED.value
    )
    blocker_reason = assignment.blocker_reason if assignment is not None else None
    dispatch_verified = bool(assignment.dispatch_verified) if assignment is not None else False

    if blocker_reason or delivery_state == DeliveryState.BLOCKED.value:
        runtime_state = RuntimeState.BLOCKED.value
    elif task_state == "done":
        runtime_state = RuntimeState.COMPLETE.value
    elif delivery_state == DeliveryState.ACCEPTED_ACTIVE.value and dispatch_verified:
        runtime_state = RuntimeState.ACTIVE.value
    elif delivery_state in {
        DeliveryState.QUEUED_UNVERIFIED.value,
        DeliveryState.RUNTIME_CREATED.value,
        DeliveryState.PROMPT_VISIBLE.value,
        DeliveryState.PAYLOAD_WRITTEN.value,
        DeliveryState.SUBMIT_SENT.value,
        DeliveryState.SUBMITTED_PENDING_ACCEPTANCE.value,
    }:
        runtime_state = RuntimeState.STARTING.value
    elif delivery_state == DeliveryState.FAILED.value:
        runtime_state = RuntimeState.FAILED.value
    else:
        runtime_state = RuntimeState.IDLE.value

    handoff = handoff_from_assignment(
        assignment,
        project_id=tg.project_id,
        task_id=tg.id,
    )
    return RuntimeTruthSummary(
        task_state=task_state,
        runtime_state=runtime_state,
        delivery_state=delivery_state,
        assignment_status=assignment.status if assignment is not None else None,
        dispatch_verified=dispatch_verified,
        blocker_reason=blocker_reason,
        last_activity_at=assignment.last_activity_at if assignment is not None else None,
        evidence={
            "assignment_id": assignment.assignment_id if assignment is not None else None,
            "ace_session_id": assignment.ace_session_id if assignment is not None else None,
            "assigned_task_id": assignment.assigned_task_id if assignment is not None else None,
            "managed_handoff": handoff.as_dict(),
        },
    )


def _active_assignment_for_task(
    assignments: list[Any], task_graph_id: str
) -> Any | None:
    active = [
        assignment
        for assignment in assignments
        if assignment.task_graph_id == task_graph_id
        and assignment.status in {"assigned", "working"}
    ]
    if active:
        return active[0]
    historical = [
        assignment for assignment in assignments if assignment.task_graph_id == task_graph_id
    ]
    return historical[0] if historical else None


def _to_response(tg: Any, assignment: Any | None = None) -> TaskGraphResponse:
    truth = _runtime_truth_for_task(tg, assignment)
    return TaskGraphResponse(
        id=tg.id,
        project_id=tg.project_id,
        title=tg.title,
        description=tg.description,
        status=tg.status,
        task_state=truth.task_state,
        runtime_state=truth.runtime_state,
        delivery_state=truth.delivery_state,
        assignment_status=truth.assignment_status,
        dispatch_verified=truth.dispatch_verified,
        blocker_reason=truth.blocker_reason,
        last_activity_at=truth.last_activity_at,
        runtime_truth=truth,
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
    project_id: str,
    request: Request,
) -> list[TaskGraphResponse]:
    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    items = await db_ops.list_task_graphs(db, project_id=project_id)
    assignments = await db_ops.list_task_assignments(db)
    return [_to_response(tg, _active_assignment_for_task(assignments, tg.id)) for tg in items]


@router.post(
    "/projects/{project_id}/task-graphs",
    response_model=TaskGraphResponse,
    status_code=201,
)
async def create_task_graph(
    project_id: str,
    body: CreateTaskGraphRequest,
    request: Request,
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
    assignments = await db_ops.list_task_assignments(db, task_graph_id=tg.id)
    return _to_response(tg, _active_assignment_for_task(assignments, tg.id))


@router.get(
    "/task-graphs/{task_graph_id}",
    response_model=TaskGraphResponse,
)
async def get_task_graph(
    task_graph_id: str,
    request: Request,
) -> TaskGraphResponse:
    db = await _get_db(request)
    tg = await db_ops.get_task_graph(db, task_graph_id)
    if tg is None:
        raise HTTPException(
            status_code=404,
            detail=f"TaskGraph {task_graph_id} not found",
        )
    assignments = await db_ops.list_task_assignments(db, task_graph_id=tg.id)
    return _to_response(tg, _active_assignment_for_task(assignments, tg.id))


@router.patch(
    "/task-graphs/{task_graph_id}",
    response_model=TaskGraphResponse,
)
async def update_task_graph(
    task_graph_id: str,
    body: UpdateTaskGraphRequest,
    request: Request,
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
            status_code=404,
            detail=f"TaskGraph {task_graph_id} not found",
        )
    assignments = await db_ops.list_task_assignments(db, task_graph_id=tg.id)
    return _to_response(tg, _active_assignment_for_task(assignments, tg.id))


@router.patch(
    "/task-graphs/{task_graph_id}/status",
    response_model=TaskGraphResponse,
)
async def transition_task_graph_status(
    task_graph_id: str,
    body: StatusTransitionRequest,
    request: Request,
) -> TaskGraphResponse:
    db = await _get_db(request)
    try:
        tg = await db_ops.update_task_graph_status(db, task_graph_id, body.status)
    except LifecycleTransitionError as e:
        raise HTTPException(status_code=409, detail=_transition_error_detail(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    if tg is None:
        raise HTTPException(
            status_code=404,
            detail=f"TaskGraph {task_graph_id} not found",
        )
    assignments = await db_ops.list_task_assignments(db, task_graph_id=tg.id)
    return _to_response(tg, _active_assignment_for_task(assignments, tg.id))


@router.delete("/task-graphs/{task_graph_id}", status_code=204)
async def delete_task_graph(
    task_graph_id: str,
    request: Request,
) -> None:
    db = await _get_db(request)
    deleted = await db_ops.delete_task_graph(db, task_graph_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"TaskGraph {task_graph_id} not found",
        )


# ---------------------------------------------------------------------------
# Task Assignment routes (idempotent assignments)
# ---------------------------------------------------------------------------


class AssignTaskRequest(BaseModel):
    ace_session_id: str
    assignment_id: str


class TaskAssignmentResponse(BaseModel):
    id: str
    task_graph_id: str
    ace_session_id: str
    assignment_id: str
    status: str
    dispatch_delivery_state: str
    dispatch_verified: bool
    assigned_task_id: str | None = None
    blocker_reason: str | None = None
    last_activity_at: str | None = None
    created_at: str
    updated_at: str


class AssignmentStatusRequest(BaseModel):
    status: str


def _to_assignment_response(a: Any) -> TaskAssignmentResponse:
    return TaskAssignmentResponse(
        id=a.id,
        task_graph_id=a.task_graph_id,
        ace_session_id=a.ace_session_id,
        assignment_id=a.assignment_id,
        status=a.status,
        dispatch_delivery_state=a.dispatch_delivery_state,
        dispatch_verified=a.dispatch_verified,
        assigned_task_id=a.assigned_task_id,
        blocker_reason=a.blocker_reason,
        last_activity_at=a.last_activity_at,
        created_at=a.created_at,
        updated_at=a.updated_at,
    )


@router.post(
    "/task-graphs/{task_graph_id}/assign",
    response_model=TaskAssignmentResponse,
)
async def assign_task(
    task_graph_id: str,
    body: AssignTaskRequest,
    request: Request,
) -> TaskAssignmentResponse:
    """Idempotently assign an Ace to a task graph entry.

    Returns 200 with the assignment record.  If the same assignment_id
    was already used, the existing record is returned (no-op).
    """
    db = await _get_db(request)
    try:
        assignment, _created = await db_ops.assign_task(
            db,
            task_graph_id,
            body.ace_session_id,
            body.assignment_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return _to_assignment_response(assignment)


@router.get(
    "/task-graphs/{task_graph_id}/assignments",
    response_model=list[TaskAssignmentResponse],
)
async def list_task_assignments(
    task_graph_id: str,
    request: Request,
) -> list[TaskAssignmentResponse]:
    db = await _get_db(request)
    items = await db_ops.list_task_assignments(db, task_graph_id=task_graph_id)
    return [_to_assignment_response(a) for a in items]


@router.patch(
    "/task-assignments/{assignment_id}/status",
    response_model=TaskAssignmentResponse,
)
async def transition_assignment_status(
    assignment_id: str,
    body: AssignmentStatusRequest,
    request: Request,
) -> TaskAssignmentResponse:
    db = await _get_db(request)
    try:
        assignment = await db_ops.update_task_assignment_status(
            db,
            assignment_id,
            body.status,
        )
    except LifecycleTransitionError as e:
        raise HTTPException(status_code=409, detail=_transition_error_detail(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    if assignment is None:
        raise HTTPException(
            status_code=404,
            detail=f"Assignment {assignment_id} not found",
        )
    return _to_assignment_response(assignment)

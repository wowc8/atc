"""Leader REST endpoints — decomposition, Ace orchestration, and progress.

Routes:
  POST   /api/projects/{project_id}/leader/decompose     → decompose goal into tasks
  POST   /api/projects/{project_id}/leader/spawn-aces     → spawn Aces for ready tasks
  POST   /api/projects/{project_id}/leader/instruct       → send instruction to an Ace
  POST   /api/projects/{project_id}/leader/task-done      → mark a task as done
  POST   /api/projects/{project_id}/leader/task-failed    → mark a task as failed
  GET    /api/projects/{project_id}/leader/progress       → get progress summary
  POST   /api/projects/{project_id}/leader/cleanup        → destroy all active Aces
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from atc.leader.decomposer import TaskSpec, decompose_goal
from atc.leader.orchestrator import LeaderOrchestrator
from atc.state import db as db_ops

router = APIRouter()

# Store orchestrators per project (in-process cache)
_orchestrators: dict[str, LeaderOrchestrator] = {}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class TaskSpecRequest(BaseModel):
    title: str
    description: str | None = None
    priority: int = 0
    dependencies: list[str] | None = None


class DecomposeRequest(BaseModel):
    task_specs: list[TaskSpecRequest]


class InstructRequest(BaseModel):
    task_graph_id: str
    instruction: str


class TaskDoneRequest(BaseModel):
    task_graph_id: str


class TaskFailedRequest(BaseModel):
    task_graph_id: str
    reason: str | None = None


class DecomposeResponse(BaseModel):
    project_id: str
    goal: str
    task_graphs: list[dict[str, Any]]
    error: str | None = None


class SpawnAcesResponse(BaseModel):
    spawned: list[dict[str, Any]]


class ProgressResponse(BaseModel):
    leader_id: str
    project_id: str
    total: int
    done: int
    in_progress: int
    todo: int
    all_done: bool
    progress_pct: int
    assignments: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_db(request: Request) -> Any:
    return request.app.state.db


def _get_event_bus(request: Request) -> Any:
    return getattr(request.app.state, "event_bus", None)


async def _broadcast_tower_progress(request: Request) -> None:
    tower = getattr(request.app.state, "tower_controller", None)
    if tower is None:
        return
    with __import__("contextlib").suppress(Exception):
        await tower.get_progress()


async def _get_or_create_orchestrator(
    request: Request,
    project_id: str,
) -> LeaderOrchestrator:
    """Retrieve or create a LeaderOrchestrator for a project."""
    if project_id in _orchestrators:
        return _orchestrators[project_id]

    db = await _get_db(request)
    event_bus = _get_event_bus(request)

    leader = await db_ops.get_leader_by_project(db, project_id)
    if leader is None:
        raise HTTPException(
            status_code=404,
            detail=f"No leader found for project {project_id}",
        )

    from atc.tracking.resources import ResourceGovernor
    settings = getattr(request.app.state, "settings", None)
    if settings is not None:
        governor = ResourceGovernor.from_config(settings.resource_monitor)
        max_aces = settings.resource_monitor.max_concurrent_aces
    else:
        governor = ResourceGovernor()
        max_aces = 3

    orch = LeaderOrchestrator(
        project_id=project_id,
        leader_id=leader.id,
        conn=db,
        event_bus=event_bus,
        _max_concurrent_aces=max_aces,
        _governor=governor,
    )

    # Subscribe to session status changes for Ace monitoring
    if event_bus:
        event_bus.subscribe(
            "session_status_changed",
            orch.on_session_status_changed,
        )

    _orchestrators[project_id] = orch
    return orch


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/leader/decompose",
    response_model=DecomposeResponse,
    status_code=201,
)
async def decompose(
    project_id: str,
    body: DecomposeRequest,
    request: Request,
) -> DecomposeResponse:
    """Decompose a project's goal into task graph entries.

    Reads the context package from the leader row and creates task_graph
    entries for each provided task specification.
    """
    db = await _get_db(request)

    leader = await db_ops.get_leader_by_project(db, project_id)
    if leader is None:
        raise HTTPException(
            status_code=404,
            detail=f"No leader found for project {project_id}",
        )

    # Build context package from leader row
    context_package = leader.context or {
        "project_id": project_id,
        "goal": leader.goal or "",
    }

    specs = [
        TaskSpec(
            title=s.title,
            description=s.description,
            priority=s.priority,
            dependencies=s.dependencies,
        )
        for s in body.task_specs
    ]

    result = await decompose_goal(db, context_package, specs)

    if result.error:
        raise HTTPException(status_code=422, detail=result.error)

    # Broadcast task graphs to frontend so TaskBoard updates immediately
    ws_hub = getattr(request.app.state, "ws_hub", None)
    if ws_hub is not None:
        tg_data = [
            {
                "id": tg.id,
                "title": tg.title,
                "description": tg.description,
                "status": tg.status,
                "dependencies": tg.dependencies,
                "project_id": project_id,
                "assigned_ace_id": tg.assigned_ace_id,
                "created_at": tg.created_at,
                "updated_at": tg.updated_at,
            }
            for tg in result.task_graphs
        ]
        await ws_hub.broadcast(
            "state",
            {"task_graphs_updated": True, "project_id": project_id, "task_graphs": tg_data},
        )

    await _broadcast_tower_progress(request)

    return DecomposeResponse(
        project_id=result.project_id,
        goal=result.goal,
        task_graphs=[
            {
                "id": tg.id,
                "title": tg.title,
                "description": tg.description,
                "status": tg.status,
                "dependencies": tg.dependencies,
            }
            for tg in result.task_graphs
        ],
    )


@router.post(
    "/projects/{project_id}/leader/spawn-aces",
    response_model=SpawnAcesResponse,
)
async def spawn_aces(
    project_id: str,
    request: Request,
) -> SpawnAcesResponse:
    """Spawn Ace sessions for all ready (unblocked) tasks."""
    orch = await _get_or_create_orchestrator(request, project_id)
    assignments = await orch.spawn_aces_for_ready_tasks()
    await _broadcast_tower_progress(request)

    return SpawnAcesResponse(
        spawned=[
            {
                "ace_session_id": a.ace_session_id,
                "task_graph_id": a.task_graph_id,
                "task_title": a.task_title,
            }
            for a in assignments
        ],
    )


@router.post("/projects/{project_id}/leader/instruct")
async def instruct_ace(
    project_id: str,
    body: InstructRequest,
    request: Request,
) -> dict[str, str]:
    """Send a work instruction to the Ace assigned to a task."""
    orch = await _get_or_create_orchestrator(request, project_id)

    try:
        await orch.send_instruction_to_ace(
            body.task_graph_id,
            body.instruction,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"status": "sent"}


@router.post("/projects/{project_id}/leader/task-done")
async def task_done(
    project_id: str,
    body: TaskDoneRequest,
    request: Request,
) -> dict[str, str]:
    """Mark a task graph entry as done and clean up its Ace."""
    orch = await _get_or_create_orchestrator(request, project_id)
    await orch.mark_task_done(body.task_graph_id)
    await _broadcast_tower_progress(request)
    return {"status": "done"}


@router.post("/projects/{project_id}/leader/task-failed")
async def task_failed(
    project_id: str,
    body: TaskFailedRequest,
    request: Request,
) -> dict[str, str]:
    """Mark a task graph entry as failed and allow retry."""
    orch = await _get_or_create_orchestrator(request, project_id)
    await orch.mark_task_failed(body.task_graph_id, reason=body.reason)
    await _broadcast_tower_progress(request)
    return {"status": "failed"}


@router.get(
    "/projects/{project_id}/leader/progress",
    response_model=ProgressResponse,
)
async def get_progress(
    project_id: str,
    request: Request,
) -> ProgressResponse:
    """Return a summary of the Leader's task graph progress."""
    orch = await _get_or_create_orchestrator(request, project_id)
    progress = await orch.get_progress()
    return ProgressResponse(**progress)


@router.post("/projects/{project_id}/leader/cleanup")
async def cleanup(
    project_id: str,
    request: Request,
) -> dict[str, str]:
    """Destroy all active Ace sessions for a project's Leader."""
    orch = await _get_or_create_orchestrator(request, project_id)
    await orch.cleanup()

    # Remove the orchestrator from cache
    _orchestrators.pop(project_id, None)

    return {"status": "cleaned_up"}

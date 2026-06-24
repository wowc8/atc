"""Leader REST endpoints — decomposition, Ace orchestration, and progress.

Routes:
  POST   /api/projects/{project_id}/leader/decompose     → decompose goal into tasks
  POST   /api/projects/{project_id}/leader/spawn-aces     → spawn Aces for ready tasks
  POST   /api/projects/{project_id}/leader/assign-task    → spawn one Ace for a task
  POST   /api/projects/{project_id}/leader/instruct       → send instruction to an Ace
  POST   /api/projects/{project_id}/leader/task-done      → mark a task as done
  POST   /api/projects/{project_id}/leader/task-failed    → mark a task as failed
  GET    /api/projects/{project_id}/leader/progress       → get progress summary
  POST   /api/projects/{project_id}/leader/cleanup        → destroy all active Aces
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from atc.api.delivery import delivery_response
from atc.leader.decomposer import TaskSpec, decompose_goal
from atc.leader.orchestrator import LeaderOrchestrator
from atc.orchestration.boundaries import enforce_boundary
from atc.state import db as db_ops
from atc.state.transitions import LifecycleTransitionError

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


class AssignTaskRequest(BaseModel):
    task_graph_id: str


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
    startup_expectation: dict[str, Any] = Field(default_factory=dict)


class AssignTaskResponse(BaseModel):
    assigned: dict[str, Any] | None = None
    error: str | None = None
    startup_expectation: dict[str, Any] = Field(default_factory=dict)


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
    leader_state: str = "working"
    handoff_verified: bool = False
    ace_blockers: list[dict[str, Any]] = Field(default_factory=list)
    tower_recommended_action: str = "wait_for_leader_or_completion_hook"
    tower_allowed_actions: list[str] = Field(default_factory=list)
    tower_must_not: list[str] = Field(default_factory=list)
    blocker_cycle_count: int = 0
    should_nudge_leader: bool = False
    should_escalate_to_operator: bool = False
    escalation_reason: str | None = None
    blocked_transition_errors: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_db(request: Request) -> Any:
    return request.app.state.db


def _get_event_bus(request: Request) -> Any:
    return getattr(request.app.state, "event_bus", None)


def _transition_error_detail(exc: LifecycleTransitionError) -> dict[str, object]:
    """Return normalized lifecycle-transition error detail for API callers."""
    return exc.to_detail()


def _ace_startup_expectation(project_id: str, ace_id: str = "<ace-id>") -> dict[str, object]:
    return {
        "folder_trust_prompt_expected": True,
        "must_check_health_before_instruction_nudge": True,
        "health_command": f"atc ace health --project-id {project_id} --ace-id {ace_id}",
        "recover_command": (
            f"atc ace recover --project-id {project_id} --ace-id {ace_id} --dry-run"
        ),
        "hard_rule": (
            "Leader must expect every newly spawned managed Ace may stop at a folder "
            "trust/startup prompt. Check Ace health and clear classified startup blockers "
            "before resending instructions or treating acknowledgement as task acceptance."
        ),
    }


async def _broadcast_tower_progress(request: Request) -> None:
    """Broadcast that Leader-owned task state changed without Tower polling it."""
    ws_hub = getattr(request.app.state, "ws_hub", None)
    if ws_hub is None:
        return
    with __import__("contextlib").suppress(Exception):
        await ws_hub.broadcast(
            "tower",
            {
                "type": "leader_task_activity",
                "message": "Leader-owned task state changed; Tower is not polling after handoff.",
            },
        )


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
    await enforce_boundary(
        request,
        action="tasks.assign",
        target_role="ace",
        project_id=project_id,
        metadata={"bulk": True},
    )
    orch = await _get_or_create_orchestrator(request, project_id)
    assignments = await orch.spawn_aces_for_ready_tasks()
    await _broadcast_tower_progress(request)

    return SpawnAcesResponse(
        spawned=[
            {
                "ace_session_id": a.ace_session_id,
                "task_graph_id": a.task_graph_id,
                "task_title": a.task_title,
                "assignment_id": a.assignment_id,
                "status": a.status,
                "startup_expectation": _ace_startup_expectation(project_id, a.ace_session_id),
            }
            for a in assignments
        ],
        startup_expectation=_ace_startup_expectation(project_id),
    )


@router.post(
    "/projects/{project_id}/leader/assign-task",
    response_model=AssignTaskResponse,
)
async def assign_task(
    project_id: str,
    body: AssignTaskRequest,
    request: Request,
) -> AssignTaskResponse:
    """Spawn or reuse one Ace assignment for a specific ready task graph entry."""
    await enforce_boundary(
        request,
        action="tasks.assign",
        target_role="ace",
        project_id=project_id,
        metadata={"task_graph_id": body.task_graph_id},
    )
    orch = await _get_or_create_orchestrator(request, project_id)
    try:
        assignment = await orch.spawn_ace_for_task(body.task_graph_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    await _broadcast_tower_progress(request)

    if assignment is None:
        return AssignTaskResponse(
            error="assignment_not_created",
            startup_expectation=_ace_startup_expectation(project_id),
        )
    return AssignTaskResponse(
        assigned={
            "ace_session_id": assignment.ace_session_id,
            "task_graph_id": assignment.task_graph_id,
            "task_title": assignment.task_title,
            "assignment_id": assignment.assignment_id,
            "status": assignment.status,
            "startup_expectation": _ace_startup_expectation(project_id, assignment.ace_session_id),
        },
        startup_expectation=_ace_startup_expectation(project_id, assignment.ace_session_id),
    )


@router.post("/projects/{project_id}/leader/instruct")
async def instruct_ace(
    project_id: str,
    body: InstructRequest,
    request: Request,
) -> dict[str, object]:
    """Send a work instruction to the Ace assigned to a task."""
    orch = await _get_or_create_orchestrator(request, project_id)

    try:
        result = await orch.send_instruction_to_ace(
            body.task_graph_id,
            body.instruction,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LifecycleTransitionError as exc:
        raise HTTPException(status_code=409, detail=_transition_error_detail(exc)) from None

    response = delivery_response(
        result,
        fallback_state="submitted",
        message="Ace instruction submitted; provider acknowledgement is not verified",
        project_id=project_id,
        recovery=(
            "check Ace health for expected startup/folder trust blockers before resending "
            "instructions; then inspect task assignment acceptance state"
        ),
    )
    ace_id = str(response.get("session_id") or "<ace-id>")
    response["startup_expectation"] = _ace_startup_expectation(project_id, ace_id)
    return response


@router.post("/projects/{project_id}/leader/task-done")
async def task_done(
    project_id: str,
    body: TaskDoneRequest,
    request: Request,
) -> dict[str, str]:
    """Mark a task graph entry as done and clean up its Ace."""
    orch = await _get_or_create_orchestrator(request, project_id)
    try:
        await orch.mark_task_done(body.task_graph_id)
    except LifecycleTransitionError as exc:
        raise HTTPException(status_code=409, detail=_transition_error_detail(exc)) from None
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
    try:
        await orch.mark_task_failed(body.task_graph_id, reason=body.reason)
    except LifecycleTransitionError as exc:
        raise HTTPException(status_code=409, detail=_transition_error_detail(exc)) from None
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
    tower = getattr(request.app.state, "tower_controller", None)
    if tower is not None and hasattr(tower, "observe_leader_ace_blockers"):
        policy = tower.observe_leader_ace_blockers(
            project_id,
            list(progress.get("ace_blockers") or []),
        )
        progress.update(
            {
                "tower_recommended_action": policy["tower_recommended_action"],
                "tower_allowed_actions": policy["tower_allowed_actions"],
                "blocker_cycle_count": policy["blocker_cycle_count"],
                "should_nudge_leader": policy["should_nudge_leader"],
                "should_escalate_to_operator": policy["should_escalate_to_operator"],
                "escalation_reason": policy["reason"],
            }
        )
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

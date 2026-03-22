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

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from atc.core.errors import CreationFailedError
from atc.leader import leader as leader_ops
from atc.state import db as db_ops

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateProjectRequest(BaseModel):
    name: str
    description: str | None = None
    repo_path: str | None = None
    github_repo: str | None = None
    agent_provider: str = "claude_code"


class ProjectResponse(BaseModel):
    id: str
    name: str
    status: str
    description: str | None = None
    repo_path: str | None = None
    github_repo: str | None = None
    agent_provider: str = "claude_code"
    position: int = 0
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


async def _get_ws_hub(request: Request):  # noqa: ANN202
    return getattr(request.app.state, "ws_hub", None)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(request: Request) -> list[ProjectResponse]:
    db = await _get_db(request)
    projects = await db_ops.list_projects(db)
    return [ProjectResponse(**p.__dict__) for p in projects]


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(body: CreateProjectRequest, request: Request) -> ProjectResponse:
    db = await _get_db(request)
    try:
        project = await db_ops.create_project(
            db,
            body.name,
            description=body.description,
            repo_path=body.repo_path,
            github_repo=body.github_repo,
            agent_provider=body.agent_provider,
        )
    except Exception:
        logger.exception("Failed to create project %r", body.name)
        raise HTTPException(status_code=500, detail="Failed to create project") from None

    # Auto-create Leader for the project
    try:
        await db_ops.create_leader(db, project.id)
    except Exception:
        logger.exception("Failed to create leader for project %s", project.id)
        # Project was created but leader failed — don't leave the user hanging
        # Return the project anyway; leader can be created later.

    resp = ProjectResponse(**project.__dict__)

    # Broadcast project creation to all connected WebSocket clients
    ws_hub = await _get_ws_hub(request)
    if ws_hub is not None:
        try:
            await ws_hub.broadcast("state", {
                "project_created": True,
                "project": resp.model_dump(),
            })
        except Exception:
            logger.debug("Failed to broadcast project_created via WebSocket")

    return resp


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, request: Request) -> None:
    """Hard-delete a project and all associated data."""
    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await db_ops.delete_project(db, project_id)

    # Broadcast project deletion to all connected WebSocket clients
    ws_hub = await _get_ws_hub(request)
    if ws_hub is not None:
        try:
            await ws_hub.broadcast("state", {
                "project_deleted": True,
                "project_id": project_id,
            })
        except Exception:
            logger.debug("Failed to broadcast project_deleted via WebSocket")


@router.patch("/{project_id}/archive", response_model=ProjectResponse)
async def archive_project(project_id: str, request: Request) -> ProjectResponse:
    """Archive a project (hides from dashboard but preserves data)."""
    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await db_ops.archive_project(db, project_id)
    project = await db_ops.get_project(db, project_id)
    resp = ProjectResponse(**project.__dict__)  # type: ignore[union-attr]

    # Broadcast project update to all connected WebSocket clients
    ws_hub = await _get_ws_hub(request)
    if ws_hub is not None:
        try:
            await ws_hub.broadcast("state", {
                "project_updated": True,
                "project": resp.model_dump(),
            })
        except Exception:
            logger.debug("Failed to broadcast project_updated via WebSocket")

    return resp


class ReorderPositionItem(BaseModel):
    id: str
    position: int


class ReorderProjectsRequest(BaseModel):
    positions: list[ReorderPositionItem]


@router.patch("/reorder", response_model=list[ProjectResponse])
async def reorder_projects(body: ReorderProjectsRequest, request: Request) -> list[ProjectResponse]:
    """Bulk-update project positions for drag-to-reorder."""
    db = await _get_db(request)
    pairs: list[tuple[str, int]] = [(item.id, item.position) for item in body.positions]
    await db_ops.update_project_positions(db, pairs)
    projects = await db_ops.list_projects(db)
    return [ProjectResponse(**p.__dict__) for p in projects]


class UpdateProjectStatusRequest(BaseModel):
    status: str


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project_status(
    project_id: str,
    body: UpdateProjectStatusRequest,
    request: Request,
) -> ProjectResponse:
    """Update a project's status (e.g. active|paused|archived via board drag)."""
    valid_statuses = {"active", "paused", "archived"}
    if body.status not in valid_statuses:
        allowed = ", ".join(sorted(valid_statuses))
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status {body.status!r}. Must be one of: {allowed}",
        )
    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await db_ops.update_project_status(db, project_id, body.status)
    project = await db_ops.get_project(db, project_id)
    resp = ProjectResponse(**project.__dict__)  # type: ignore[union-attr]

    ws_hub = await _get_ws_hub(request)
    if ws_hub is not None:
        try:
            await ws_hub.broadcast("state", {
                "project_updated": True,
                "project": resp.model_dump(),
            })
        except Exception:
            logger.debug("Failed to broadcast project_updated via WebSocket")

    return resp


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, request: Request) -> ProjectResponse:
    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(**project.__dict__)


class UpdateProjectProviderRequest(BaseModel):
    agent_provider: str


@router.patch("/{project_id}/agent-provider")
async def update_project_provider(
    project_id: str,
    body: UpdateProjectProviderRequest,
    request: Request,
) -> ProjectResponse:
    """Update the agent provider for a project."""
    from atc.agents.factory import list_providers

    available = list_providers()
    if body.agent_provider not in available:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown provider {body.agent_provider!r}. Available: {', '.join(available)}",
        )

    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    await db_ops.update_project_agent_provider(db, project_id, body.agent_provider)
    project = await db_ops.get_project(db, project_id)
    resp = ProjectResponse(**project.__dict__)  # type: ignore[union-attr]

    # Broadcast project update to all connected WebSocket clients
    ws_hub = await _get_ws_hub(request)
    if ws_hub is not None:
        try:
            await ws_hub.broadcast("state", {
                "project_updated": True,
                "project": resp.model_dump(),
            })
        except Exception:
            logger.debug("Failed to broadcast project_updated via WebSocket")

    return resp


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
    try:
        session_id = await leader_ops.start_leader(
            db, project_id, goal=body.goal, event_bus=event_bus,
        )
    except RuntimeError as exc:
        raise CreationFailedError(str(exc)) from None

    # Seed goal and project metadata into context hub so the Context panel
    # populates regardless of whether Tower or CLI started the Leader.
    if body.goal:
        from atc.state import db as db_ops
        seed = [("goal", "text", body.goal)]
        cursor = await db.execute(
            "SELECT name, description, repo_path, github_repo FROM projects WHERE id = ?",
            (project_id,),
        )
        row = await cursor.fetchone()
        if row:
            if row[1]: seed.append(("project_description", "text", row[1]))
            if row[2]: seed.append(("repo_path", "text", row[2]))
            if row[3]: seed.append(("github_repo", "text", row[3]))
        for key, etype, val in seed:
            try:
                await db_ops.create_context_entry(
                    db, scope="project", key=key, entry_type=etype,
                    value=val, project_id=project_id, updated_by="leader-start",
                )
                await db.commit()
            except Exception:
                try:
                    await db.execute(
                        "UPDATE context_entries SET value=?, updated_at=datetime('now')"
                        " WHERE project_id=? AND key=? AND scope='project'",
                        (val, project_id, key),
                    )
                    await db.commit()
                except Exception:
                    pass

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
        await leader_ops.send_leader_message(db, project_id, body.message, event_bus=event_bus)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except RuntimeError as e:
        raise HTTPException(
            status_code=409, detail=f"Leader pane unavailable: {e}"
        ) from None
    return {"status": "sent"}


# ---------------------------------------------------------------------------
# Budget endpoints
# ---------------------------------------------------------------------------


class BudgetResponse(BaseModel):
    project_id: str
    daily_token_limit: int | None = None
    monthly_cost_limit: float | None = None
    warn_threshold: float
    current_status: str
    updated_at: str


class UpdateBudgetRequest(BaseModel):
    daily_token_limit: int | None = None
    monthly_cost_limit: float | None = None
    warn_threshold: float = 0.8


@router.get("/{project_id}/budget", response_model=BudgetResponse)
async def get_budget(project_id: str, request: Request) -> BudgetResponse:
    """Return budget config and current status for a project."""
    db = await _get_db(request)
    budget = await db_ops.get_project_budget(db, project_id)
    if budget is None:
        # Return default unconfigured budget
        from datetime import UTC, datetime

        return BudgetResponse(
            project_id=project_id,
            daily_token_limit=None,
            monthly_cost_limit=None,
            warn_threshold=0.8,
            current_status="ok",
            updated_at=datetime.now(UTC).isoformat(),
        )
    return BudgetResponse(**budget.__dict__)


@router.put("/{project_id}/budget", response_model=BudgetResponse)
async def update_budget(
    project_id: str,
    body: UpdateBudgetRequest,
    request: Request,
) -> BudgetResponse:
    """Create or update the budget limits for a project."""
    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    budget = await db_ops.upsert_project_budget(
        db,
        project_id,
        daily_token_limit=body.daily_token_limit,
        monthly_cost_limit=body.monthly_cost_limit,
        warn_threshold=body.warn_threshold,
    )
    return BudgetResponse(**budget.__dict__)


@router.post("/{project_id}/budget/reset")
async def reset_budget(project_id: str, request: Request) -> dict[str, str]:
    """Reset the budget status to 'ok' (e.g. after starting a new month)."""
    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await db_ops.update_project_budget_status(db, project_id, "ok")
    return {"status": "reset"}


# ---------------------------------------------------------------------------
# GitHub endpoints
# ---------------------------------------------------------------------------


class GitHubPRResponse(BaseModel):
    id: str
    project_id: str | None
    number: int
    title: str | None = None
    status: str | None = None
    ci_status: str | None = None
    url: str | None = None
    updated_at: str


class RateLimitResponse(BaseModel):
    limit: int
    remaining: int
    reset: int


@router.get("/{project_id}/github/prs", response_model=list[GitHubPRResponse])
async def list_github_prs(project_id: str, request: Request) -> list[GitHubPRResponse]:
    """Return the PR list for a project with CI status badges."""
    db = await _get_db(request)
    prs = await db_ops.list_github_prs(db, project_id)
    return [GitHubPRResponse(**pr.__dict__) for pr in prs]


@router.get("/{project_id}/github/rate-limit", response_model=RateLimitResponse)
async def get_github_rate_limit(project_id: str, request: Request) -> RateLimitResponse:
    """Return the current GitHub API rate limit for this project's repo."""
    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.github_repo:
        raise HTTPException(status_code=422, detail="Project has no github_repo configured")

    tracker = getattr(request.app.state, "github_tracker", None)
    if tracker is None:
        return RateLimitResponse(limit=0, remaining=0, reset=0)

    rl = tracker.get_rate_limit(project.github_repo)
    if rl is None:
        return RateLimitResponse(limit=0, remaining=0, reset=0)
    return RateLimitResponse(**rl)


@router.post("/{project_id}/github/sync")
async def sync_github(project_id: str, request: Request) -> dict[str, str]:
    """Force a GitHub sync for this project."""
    db = await _get_db(request)
    project = await db_ops.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.github_repo:
        raise HTTPException(status_code=422, detail="Project has no github_repo configured")

    tracker = getattr(request.app.state, "github_tracker", None)
    if tracker is None:
        raise HTTPException(status_code=503, detail="GitHub tracker not running")

    await tracker.poll_project(project_id, project.github_repo)
    return {"status": "synced"}

"""Tower REST endpoints — goal intake and tower status.

Routes:
  GET    /api/tower/status           → Tower status summary
  POST   /api/tower/goal             → submit new goal (creates Leader + context)
  POST   /api/tower/cancel           → cancel current goal
  GET    /api/tower/memory           → list tower memory entries
  DELETE /api/tower/memory/{key}     → forget a memory entry
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from atc.tower.controller import TowerBusyError, TowerController

router = APIRouter()


class GoalRequest(BaseModel):
    project_id: str
    goal: str


class TowerStatusResponse(BaseModel):
    status: str
    active_projects: int
    total_sessions: int
    state: str
    current_goal: str | None
    current_project_id: str | None
    current_session_id: str | None


class MemoryEntry(BaseModel):
    id: str
    key: str
    value: str
    project_id: str | None
    created_at: str
    updated_at: str


async def _get_db(request: Request):  # noqa: ANN202
    return request.app.state.db


def _get_tower(request: Request) -> TowerController:
    tower: TowerController | None = getattr(request.app.state, "tower_controller", None)
    if tower is None:
        raise HTTPException(status_code=503, detail="Tower controller not initialized")
    return tower


@router.get("/status", response_model=TowerStatusResponse)
async def tower_status(request: Request) -> TowerStatusResponse:
    """Return a summary of the Tower's current state."""
    db = await _get_db(request)
    tower = _get_tower(request)

    cursor = await db.execute("SELECT COUNT(*) FROM projects WHERE status = 'active'")
    row = await cursor.fetchone()
    active_projects = row[0] if row else 0

    cursor = await db.execute("SELECT COUNT(*) FROM sessions")
    row = await cursor.fetchone()
    total_sessions = row[0] if row else 0

    controller_status = tower.get_status()

    return TowerStatusResponse(
        status="running",
        active_projects=active_projects,
        total_sessions=total_sessions,
        state=controller_status["state"],
        current_goal=controller_status["current_goal"],
        current_project_id=controller_status["current_project_id"],
        current_session_id=controller_status["current_session_id"],
    )


@router.post("/goal")
async def submit_goal(body: GoalRequest, request: Request) -> dict:
    """Submit a new goal for a project's Leader to work on.

    The Tower controller builds a context package, starts the Leader
    session, and begins monitoring progress.
    """
    db = await _get_db(request)
    tower = _get_tower(request)

    # Verify project exists
    cursor = await db.execute("SELECT id FROM projects WHERE id = ?", (body.project_id,))
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Project {body.project_id} not found")

    try:
        result = await tower.submit_goal(body.project_id, body.goal)
    except TowerBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return result


@router.post("/cancel")
async def cancel_goal(request: Request) -> dict:
    """Cancel the current goal and stop the Leader session."""
    tower = _get_tower(request)

    if tower.state.value == "idle":
        raise HTTPException(status_code=409, detail="No active goal to cancel")

    await tower.cancel_goal()
    return {"status": "cancelled"}


@router.get("/memory", response_model=list[MemoryEntry])
async def list_memory(request: Request) -> list[MemoryEntry]:
    """List all tower memory entries."""
    db = await _get_db(request)
    cursor = await db.execute("SELECT * FROM tower_memory ORDER BY updated_at DESC")
    rows = await cursor.fetchall()
    return [MemoryEntry(**dict(r)) for r in rows]


@router.delete("/memory/{key}", status_code=204)
async def delete_memory(key: str, request: Request) -> None:
    """Delete a tower memory entry by key."""
    db = await _get_db(request)
    cursor = await db.execute("DELETE FROM tower_memory WHERE key = ?", (key,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Memory key '{key}' not found")
    await db.commit()

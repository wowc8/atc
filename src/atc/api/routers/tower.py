"""Tower REST endpoints — minimal for Milestone 1.

Routes:
  GET    /api/tower/status           → Tower status summary
  POST   /api/tower/goal             → submit new goal
  GET    /api/tower/memory           → list tower memory entries
  DELETE /api/tower/memory/{key}     → forget a memory entry
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class GoalRequest(BaseModel):
    project_id: str
    goal: str


class TowerStatusResponse(BaseModel):
    status: str
    active_projects: int
    total_sessions: int


class MemoryEntry(BaseModel):
    id: str
    key: str
    value: str
    project_id: str | None
    created_at: str
    updated_at: str


async def _get_db(request: Request):  # noqa: ANN202
    return request.app.state.db


@router.get("/status", response_model=TowerStatusResponse)
async def tower_status(request: Request) -> TowerStatusResponse:
    """Return a summary of the Tower's current state."""
    db = await _get_db(request)

    cursor = await db.execute("SELECT COUNT(*) FROM projects WHERE status = 'active'")
    row = await cursor.fetchone()
    active_projects = row[0] if row else 0

    cursor = await db.execute("SELECT COUNT(*) FROM sessions")
    row = await cursor.fetchone()
    total_sessions = row[0] if row else 0

    return TowerStatusResponse(
        status="running",
        active_projects=active_projects,
        total_sessions=total_sessions,
    )


@router.post("/goal")
async def submit_goal(body: GoalRequest, request: Request) -> dict[str, str]:
    """Submit a new goal for a project's Leader to work on."""
    db = await _get_db(request)
    event_bus = getattr(request.app.state, "event_bus", None)

    # Verify project exists
    cursor = await db.execute("SELECT id FROM projects WHERE id = ?", (body.project_id,))
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Project {body.project_id} not found")

    # Update leader goal
    await db.execute(
        "UPDATE leaders SET goal = ?, updated_at = datetime('now') WHERE project_id = ?",
        (body.goal, body.project_id),
    )
    await db.commit()

    if event_bus:
        await event_bus.publish(
            "tower_goal_submitted",
            {"project_id": body.project_id, "goal": body.goal},
        )

    return {"status": "accepted", "project_id": body.project_id}


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

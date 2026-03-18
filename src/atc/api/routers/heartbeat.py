"""Heartbeat REST endpoints.

Routes:
  POST   /api/heartbeat/{session_id}        -> record heartbeat
  GET    /api/heartbeat                      -> list all heartbeats
  GET    /api/heartbeat/{session_id}         -> get single heartbeat
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from atc.state import db as db_ops

router = APIRouter()


class HeartbeatResponse(BaseModel):
    session_id: str
    health: str
    last_heartbeat_at: str
    registered_at: str
    updated_at: str


@router.post("/heartbeat/{session_id}")
async def record_heartbeat(session_id: str, request: Request) -> dict[str, str]:
    """Record a heartbeat from an agent session (called by hooks)."""
    monitor = getattr(request.app.state, "heartbeat_monitor", None)
    if monitor is None:
        # Fallback: write directly to DB
        db = request.app.state.db
        recorded = await db_ops.record_heartbeat(db, session_id)
        if not recorded:
            await db_ops.register_heartbeat(db, session_id)
        return {"status": "ok"}

    await monitor.handle_heartbeat(session_id)
    return {"status": "ok"}


@router.get("/heartbeat", response_model=list[HeartbeatResponse])
async def list_heartbeats(request: Request) -> list[HeartbeatResponse]:
    """List all heartbeat records."""
    db = request.app.state.db
    heartbeats = await db_ops.list_heartbeats(db)
    return [
        HeartbeatResponse(
            session_id=hb.session_id,
            health=hb.health,
            last_heartbeat_at=hb.last_heartbeat_at,
            registered_at=hb.registered_at,
            updated_at=hb.updated_at,
        )
        for hb in heartbeats
    ]


@router.get("/heartbeat/{session_id}", response_model=HeartbeatResponse)
async def get_heartbeat(session_id: str, request: Request) -> HeartbeatResponse:
    """Get heartbeat for a specific session."""
    db = request.app.state.db
    hb = await db_ops.get_heartbeat(db, session_id)
    if hb is None:
        raise HTTPException(status_code=404, detail=f"No heartbeat for session {session_id}")
    return HeartbeatResponse(
        session_id=hb.session_id,
        health=hb.health,
        last_heartbeat_at=hb.last_heartbeat_at,
        registered_at=hb.registered_at,
        updated_at=hb.updated_at,
    )

"""System maintenance endpoints.

Routes:
  GET /api/system/cleanup  → run orphan session + staging dir cleanup
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from atc.core.cleanup import run_startup_cleanup

router = APIRouter()


class CleanupResult(BaseModel):
    ace: int
    manager: int
    tower: int


@router.get("/cleanup", response_model=CleanupResult)
async def trigger_cleanup(request: Request) -> CleanupResult:
    """Manually trigger orphan session and staging directory cleanup.

    Runs the same cleanup logic as startup: deletes stale ace/manager/tower
    sessions from the DB and removes their /tmp/atc-agents/ directories.
    """
    db = request.app.state.db
    totals = await run_startup_cleanup(db)
    return CleanupResult(**totals)

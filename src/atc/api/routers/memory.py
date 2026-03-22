"""Memory system REST endpoints.

Routes:
  GET    /api/memory/search                  → semantic/FTS search over tower_memory
  GET    /api/memory/ltm                     → paginated list of LTM entries
  DELETE /api/memory/ltm/{key}               → delete an LTM entry by key
  GET    /api/memory/consolidation/runs      → list consolidation run history
  POST   /api/memory/consolidation/trigger   → manually trigger consolidation
  GET    /api/memory/ace/{session_id}        → get Ace STM snapshot for a session
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from atc.memory.ace_stm import AceSTM
from atc.memory.ltm import LongTermMemory, TowerMemoryRecord

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class LTMEntryResponse(BaseModel):
    id: str
    key: str
    value: str
    project_id: str | None = None
    created_at: str
    updated_at: str


class ConsolidationRunResponse(BaseModel):
    id: str
    started_at: str
    finished_at: str | None = None
    entries_processed: int
    entries_written: int
    status: str


class TriggerResponse(BaseModel):
    ok: bool
    message: str


class AceSTMResponse(BaseModel):
    session_id: str
    content: str | None
    found: bool


class AceSTMWriteRequest(BaseModel):
    content: str
    tool_call_count: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record_to_response(rec: TowerMemoryRecord) -> LTMEntryResponse:
    return LTMEntryResponse(
        id=rec.id,
        key=rec.key,
        value=rec.value,
        project_id=rec.project_id,
        created_at=rec.created_at,
        updated_at=rec.updated_at,
    )


async def _get_db(request: Request) -> Any:
    return request.app.state.db


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/search", response_model=list[LTMEntryResponse])
async def search_memory(
    request: Request,
    q: str = Query(..., description="Search query"),
    limit: int = Query(10, ge=1, le=100, description="Max results"),
    project_id: str | None = Query(None, description="Filter by project"),
) -> list[LTMEntryResponse]:
    """Semantic search over long-term memory (falls back to FTS5 if no embeddings)."""
    db = await _get_db(request)
    results = await LongTermMemory.search_semantic(db, q, limit, project_id=project_id)
    return [_record_to_response(r) for r in results]


@router.get("/ltm", response_model=list[LTMEntryResponse])
async def list_ltm(
    request: Request,
    limit: int = Query(100, ge=1, le=1000, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
    project_id: str | None = Query(None, description="Filter by project"),
) -> list[LTMEntryResponse]:
    """List long-term memory entries with pagination."""
    db = await _get_db(request)
    records = await LongTermMemory.list_all(db, project_id=project_id, limit=limit, offset=offset)
    return [_record_to_response(r) for r in records]


@router.delete("/ltm/{key}", response_model=dict[str, bool])
async def delete_ltm_entry(key: str, request: Request) -> dict[str, bool]:
    """Delete an LTM entry by its key slug."""
    db = await _get_db(request)
    deleted = await LongTermMemory.delete(db, key)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"LTM entry not found: {key!r}")
    return {"deleted": True}


@router.get("/consolidation/runs", response_model=list[ConsolidationRunResponse])
async def list_consolidation_runs(
    request: Request,
    limit: int = Query(20, ge=1, le=200, description="Max results"),
) -> list[ConsolidationRunResponse]:
    """Return consolidation run history, most-recent first."""
    db = await _get_db(request)
    cursor = await db.execute(
        """SELECT id, started_at, finished_at, entries_processed, entries_written, status
           FROM memory_consolidation_runs
           ORDER BY started_at DESC LIMIT ?""",
        (limit,),
    )
    rows = await cursor.fetchall()
    return [
        ConsolidationRunResponse(
            id=r["id"],
            started_at=r["started_at"],
            finished_at=r["finished_at"],
            entries_processed=r["entries_processed"],
            entries_written=r["entries_written"],
            status=r["status"],
        )
        for r in rows
    ]


@router.post("/consolidation/trigger", response_model=TriggerResponse)
async def trigger_consolidation(request: Request) -> TriggerResponse:
    """Manually trigger an LTM consolidation run."""
    cron = getattr(request.app.state, "memory_cron", None)
    if cron is None:
        raise HTTPException(status_code=503, detail="Memory cron not available")
    await cron.trigger_now()
    return TriggerResponse(ok=True, message="Consolidation triggered")


@router.get("/ace/{session_id}", response_model=AceSTMResponse)
async def get_ace_stm(session_id: str, request: Request) -> AceSTMResponse:
    """Return the current STM snapshot for an Ace session."""
    db = await _get_db(request)
    content = await AceSTM.get_progress(db, session_id)
    return AceSTMResponse(
        session_id=session_id,
        content=content,
        found=content is not None,
    )


@router.post("/ace/{session_id}/write", response_model=dict[str, object])
async def write_ace_stm(
    session_id: str,
    body: AceSTMWriteRequest,
    request: Request,
) -> dict[str, object]:
    """Write (upsert) an STM progress snapshot for an Ace session.

    Called by the ``atc ace memory write`` CLI hook every 5 tool calls.
    """
    db = await _get_db(request)
    await AceSTM.write_progress(db, session_id, body.content, body.tool_call_count)
    logger.debug("STM written via API for session %s", session_id)
    return {"ok": True, "session_id": session_id}

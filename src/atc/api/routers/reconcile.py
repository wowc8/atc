"""Runtime/orchestration reconciliation endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from atc.session.reconcile import reconcile_runtime_state

router = APIRouter()


class ReconcileRequest(BaseModel):
    repair: bool = False


class ReconcileResponse(BaseModel):
    repair: bool
    summary: dict[str, int]
    findings: list[dict[str, Any]]


@router.post("/reconcile", response_model=ReconcileResponse)
async def reconcile_state(body: ReconcileRequest, request: Request) -> ReconcileResponse:
    """Detect and optionally repair DB/runtime drift.

    Use ``repair=false`` for a dry structured scan. Use ``repair=true`` to apply
    safe repairs: stale active sessions are marked disconnected and orphaned
    active task graph entries are reset for reassignment.
    """

    result = await reconcile_runtime_state(request.app.state.db, repair=body.repair)
    payload = result.as_dict()
    return ReconcileResponse(**payload)

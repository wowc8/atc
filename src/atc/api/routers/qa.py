"""QA loop REST endpoints.

Routes:
  GET  /api/qa/runs              → list qa_loop_runs (filter: project_id, status)
  GET  /api/qa/runs/{id}         → single run detail
  POST /api/qa/trigger           → manually trigger QA for a PR
  GET  /api/qa/status/{pr_id}    → current QA status for a PR
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class QARunResponse(BaseModel):
    id: str
    project_id: str
    pr_id: str
    iteration: int
    status: str
    failure_count: int
    test_output: str | None
    created_at: str
    updated_at: str


class QAStatusResponse(BaseModel):
    pr_id: str
    qa_status: str
    latest_run: QARunResponse | None


class QATriggerRequest(BaseModel):
    pr_id: str
    project_id: str


class QATriggerResponse(BaseModel):
    pr_id: str
    queued: bool
    message: str


# ---------------------------------------------------------------------------
# GET /api/qa/runs
# ---------------------------------------------------------------------------


@router.get("/runs", response_model=list[QARunResponse])
async def list_qa_runs(
    request: Request,
    project_id: str | None = None,
    status: str | None = None,
    pr_id: str | None = None,
    limit: int = 100,
) -> list[QARunResponse]:
    """List QA loop runs, optionally filtered by project_id, status, or pr_id."""
    db = request.app.state.db

    clauses: list[str] = []
    params: list[Any] = []

    if project_id is not None:
        clauses.append("project_id = ?")
        params.append(project_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if pr_id is not None:
        clauses.append("pr_id = ?")
        params.append(pr_id)

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit)

    cursor = await db.execute(
        f"SELECT * FROM qa_loop_runs{where} ORDER BY created_at DESC LIMIT ?",  # noqa: S608
        params,
    )
    rows = await cursor.fetchall()
    return [_row_to_response(dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# GET /api/qa/runs/{run_id}
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}", response_model=QARunResponse)
async def get_qa_run(request: Request, run_id: str) -> QARunResponse:
    """Return a single QA run by its id."""
    db = request.app.state.db
    cursor = await db.execute("SELECT * FROM qa_loop_runs WHERE id = ?", (run_id,))
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"QA run {run_id!r} not found")
    return _row_to_response(dict(row))


# ---------------------------------------------------------------------------
# POST /api/qa/trigger
# ---------------------------------------------------------------------------


@router.post("/trigger", response_model=QATriggerResponse)
async def trigger_qa(request: Request, body: QATriggerRequest) -> QATriggerResponse:
    """Manually queue QA for a PR by setting qa_status to 'needs_rerun'.

    The QALoopController will pick it up on its next poll cycle.
    """
    db = request.app.state.db

    # Verify the PR exists.
    cursor = await db.execute(
        "SELECT id, qa_status FROM github_prs WHERE id = ?", (body.pr_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"PR {body.pr_id!r} not found"
        )

    current_status = str(row[1]) if row[1] else "pending"
    if current_status == "running":
        return QATriggerResponse(
            pr_id=body.pr_id,
            queued=False,
            message="QA is already running for this PR",
        )

    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat()
    await db.execute(
        "UPDATE github_prs SET qa_status = 'needs_rerun', updated_at = ? WHERE id = ?",
        (now, body.pr_id),
    )
    await db.commit()

    logger.info("QA triggered manually for PR %s (project=%s)", body.pr_id, body.project_id)
    return QATriggerResponse(
        pr_id=body.pr_id,
        queued=True,
        message="QA queued — will run on next poll cycle",
    )


# ---------------------------------------------------------------------------
# GET /api/qa/status/{pr_id}
# ---------------------------------------------------------------------------


@router.get("/status/{pr_id}", response_model=QAStatusResponse)
async def get_qa_status(request: Request, pr_id: str) -> QAStatusResponse:
    """Return the current QA status for a PR plus the latest run detail."""
    db = request.app.state.db

    cursor = await db.execute(
        "SELECT qa_status FROM github_prs WHERE id = ?", (pr_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"PR {pr_id!r} not found")

    qa_status = str(row[0]) if row[0] else "pending"

    cursor = await db.execute(
        "SELECT * FROM qa_loop_runs WHERE pr_id = ? ORDER BY iteration DESC LIMIT 1",
        (pr_id,),
    )
    run_row = await cursor.fetchone()
    latest_run = _row_to_response(dict(run_row)) if run_row is not None else None

    return QAStatusResponse(pr_id=pr_id, qa_status=qa_status, latest_run=latest_run)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_response(row: dict[str, Any]) -> QARunResponse:
    return QARunResponse(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        pr_id=str(row["pr_id"]),
        iteration=int(row["iteration"]),
        status=str(row["status"]),
        failure_count=int(row.get("failure_count") or 0),
        test_output=row.get("test_output"),
        created_at=str(row.get("created_at", "")),
        updated_at=str(row.get("updated_at", "")),
    )

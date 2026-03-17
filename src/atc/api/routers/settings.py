"""Settings router — export/import backup endpoints.

Routes:
  POST /api/settings/export/{project_id}  → export single project as zip
  POST /api/settings/export-all           → export all projects as zip
  POST /api/settings/import               → import project from zip
  POST /api/settings/import-all           → import all from full backup zip
"""

from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from atc.backup.service import export_all, export_project, import_all, import_project

router = APIRouter()
logger = logging.getLogger(__name__)


async def _get_db(request: Request):  # noqa: ANN202
    return request.app.state.db


# ---------------------------------------------------------------------------
# Export endpoints
# ---------------------------------------------------------------------------


@router.post("/export/{project_id}")
async def export_project_endpoint(project_id: str, request: Request) -> Response:
    """Export a single project as a .atc-backup.zip."""
    db = await _get_db(request)
    try:
        zip_bytes = await export_project(db, project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{project_id}.atc-backup.zip"'},
    )


@router.post("/export-all")
async def export_all_endpoint(request: Request) -> Response:
    """Export all projects as a single .atc-backup.zip."""
    db = await _get_db(request)
    zip_bytes = await export_all(db)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="atc-full-backup.atc-backup.zip"'},
    )


# ---------------------------------------------------------------------------
# Import endpoints
# ---------------------------------------------------------------------------


class ImportRequest(BaseModel):
    """Import request with base64-encoded zip data."""

    data: str  # base64-encoded zip
    target_project_id: str | None = None


class ImportAllRequest(BaseModel):
    """Import-all request with base64-encoded zip data."""

    data: str  # base64-encoded zip


@router.post("/import")
async def import_project_endpoint(
    body: ImportRequest, request: Request,
) -> dict[str, object]:
    """Import a project from a base64-encoded zip.

    If target_project_id is given and exists, auto-backup + replace.
    Otherwise creates a new project.
    """
    db = await _get_db(request)
    try:
        raw = base64.b64decode(body.data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid base64 data: {e}") from None

    try:
        result = await import_project(
            db, raw, target_project_id=body.target_project_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None

    return result


@router.post("/import-all")
async def import_all_endpoint(
    body: ImportAllRequest, request: Request,
) -> dict[str, object]:
    """Import all projects from a base64-encoded full backup zip."""
    db = await _get_db(request)
    try:
        raw = base64.b64decode(body.data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid base64 data: {e}") from None

    try:
        result = await import_all(db, raw)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None

    return result

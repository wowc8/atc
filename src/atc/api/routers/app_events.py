"""App event log REST endpoints.

Routes:
  GET    /api/app-events           -> list/filter app events
  GET    /api/app-events/count     -> count matching events
  POST   /api/app-events/export    -> export events as downloadable zip
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from atc.core.app_events import count_events, export_events_json, list_events

router = APIRouter()


class AppEventResponse(BaseModel):
    id: str
    level: str
    category: str
    message: str
    detail: dict[str, Any] | None = None
    project_id: str | None = None
    session_id: str | None = None
    created_at: str


class EventCountResponse(BaseModel):
    count: int


async def _get_db(request: Request) -> Any:
    return request.app.state.db


@router.get("/app-events/count", response_model=EventCountResponse)
async def get_event_count(
    request: Request,
    level: str | None = Query(None),
    category: str | None = Query(None),
    project_id: str | None = Query(None),
) -> EventCountResponse:
    db = await _get_db(request)
    total = await count_events(db, level=level, category=category, project_id=project_id)
    return EventCountResponse(count=total)


@router.get("/app-events", response_model=list[AppEventResponse])
async def get_app_events(
    request: Request,
    level: str | None = Query(None),
    category: str | None = Query(None),
    project_id: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[AppEventResponse]:
    db = await _get_db(request)
    events = await list_events(
        db,
        level=level,
        category=category,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )
    return [AppEventResponse(**e) for e in events]


@router.post("/app-events/export")
async def export_app_events(
    request: Request,
    level: str | None = Query(None),
    category: str | None = Query(None),
    project_id: str | None = Query(None),
) -> StreamingResponse:
    db = await _get_db(request)
    events = await export_events_json(db, level=level, category=category, project_id=project_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        payload = json.dumps(events, indent=2, default=str)
        zf.writestr("app_events.json", payload)
    buf.seek(0)
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    filename = f"atc-logs-{date_str}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

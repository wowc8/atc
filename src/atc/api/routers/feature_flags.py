"""Feature flags REST endpoints.

Routes:
  GET    /api/feature-flags           -> list all flags
  GET    /api/feature-flags/{key}     -> get flag by key
  POST   /api/feature-flags           -> create a new flag
  PUT    /api/feature-flags/{key}     -> update a flag (toggle, rename, etc.)
  DELETE /api/feature-flags/{key}     -> delete a flag
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from atc.state import db as db_ops

router = APIRouter()


async def _get_db(request: Request):  # noqa: ANN202
    return request.app.state.db


class FeatureFlagResponse(BaseModel):
    id: str
    key: str
    name: str
    description: str | None = None
    enabled: bool
    metadata: str | None = None
    created_at: str
    updated_at: str


class CreateFlagRequest(BaseModel):
    key: str
    name: str
    description: str | None = None
    enabled: bool = False
    metadata: str | None = None


class UpdateFlagRequest(BaseModel):
    enabled: bool | None = None
    name: str | None = None
    description: str | None = None
    metadata: str | None = None


@router.get("", response_model=list[FeatureFlagResponse])
async def list_feature_flags(request: Request) -> list[FeatureFlagResponse]:
    db = await _get_db(request)
    flags = await db_ops.list_feature_flags(db)
    return [FeatureFlagResponse(**f.__dict__) for f in flags]


@router.get("/{key}", response_model=FeatureFlagResponse)
async def get_feature_flag(key: str, request: Request) -> FeatureFlagResponse:
    db = await _get_db(request)
    flag = await db_ops.get_feature_flag(db, key)
    if flag is None:
        raise HTTPException(status_code=404, detail=f"Feature flag '{key}' not found")
    return FeatureFlagResponse(**flag.__dict__)


@router.post("", response_model=FeatureFlagResponse, status_code=201)
async def create_feature_flag(
    body: CreateFlagRequest,
    request: Request,
) -> FeatureFlagResponse:
    db = await _get_db(request)
    existing = await db_ops.get_feature_flag(db, body.key)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Feature flag '{body.key}' already exists",
        )
    flag = await db_ops.create_feature_flag(
        db,
        key=body.key,
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        metadata=body.metadata,
    )
    return FeatureFlagResponse(**flag.__dict__)


@router.put("/{key}", response_model=FeatureFlagResponse)
async def update_feature_flag(
    key: str,
    body: UpdateFlagRequest,
    request: Request,
) -> FeatureFlagResponse:
    db = await _get_db(request)
    kwargs: dict[str, Any] = {}
    if body.enabled is not None:
        kwargs["enabled"] = body.enabled
    if body.name is not None:
        kwargs["name"] = body.name
    if body.description is not None:
        kwargs["description"] = body.description
    if body.metadata is not None:
        kwargs["metadata"] = body.metadata
    flag = await db_ops.update_feature_flag(db, key, **kwargs)  # type: ignore[arg-type]
    if flag is None:
        raise HTTPException(status_code=404, detail=f"Feature flag '{key}' not found")
    return FeatureFlagResponse(**flag.__dict__)


@router.delete("/{key}", status_code=204)
async def delete_feature_flag(key: str, request: Request) -> None:
    db = await _get_db(request)
    deleted = await db_ops.delete_feature_flag(db, key)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Feature flag '{key}' not found")

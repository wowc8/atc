from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class OrchestrationRole(str, Enum):
    TOWER = "tower"
    LEADER = "leader"
    ACE = "ace"


class OrchestrationStatus(str, Enum):
    PENDING = "pending"
    STARTING = "starting"
    READY = "ready"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_BACKGROUND = "waiting_background"
    BLOCKED = "blocked"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


_ROLE_MAP: dict[str, OrchestrationRole] = {
    "tower": OrchestrationRole.TOWER,
    "manager": OrchestrationRole.LEADER,
    "leader": OrchestrationRole.LEADER,
    "ace": OrchestrationRole.ACE,
}

_STATUS_MAP: dict[str, OrchestrationStatus] = {
    "connecting": OrchestrationStatus.STARTING,
    "idle": OrchestrationStatus.READY,
    "working": OrchestrationStatus.RUNNING,
    "waiting": OrchestrationStatus.WAITING_INPUT,
    "paused": OrchestrationStatus.BLOCKED,
    "disconnected": OrchestrationStatus.FAILED,
    "error": OrchestrationStatus.FAILED,
}


def normalize_role(raw_session_type: str) -> OrchestrationRole:
    try:
        return _ROLE_MAP[raw_session_type]
    except KeyError as exc:
        raise ValueError(
            f"Unknown session type for orchestration role mapping: {raw_session_type}"
        ) from exc


def normalize_status(raw_status: str) -> OrchestrationStatus:
    try:
        return _STATUS_MAP[raw_status]
    except KeyError as exc:
        raise ValueError(
            f"Unknown session status for orchestration status mapping: {raw_status}"
        ) from exc


class SpawnLeaderRequest(BaseModel):
    project_id: str
    goal: str
    provider: str | None = None
    parent_session_id: str | None = None
    idempotency_key: str
    reuse_existing_idle: bool = True
    require_clean_scope: bool = False
    context: dict[str, Any] | None = None


class SpawnAceRequest(BaseModel):
    project_id: str
    instruction: str
    idempotency_key: str
    task_id: str | None = None
    parent_session_id: str | None = None
    provider: str | None = None
    host: str | None = None
    context: dict[str, Any] | None = None


class SendInstructionRequest(BaseModel):
    session_id: str
    instruction: str
    idempotency_key: str
    await_delivery: bool = True


class ListSessionsRequest(BaseModel):
    project_id: str | None = None
    role: OrchestrationRole | None = None
    parent_session_id: str | None = None
    status_in: list[OrchestrationStatus] | None = None
    active_only: bool = False
    limit: int | None = None


class WaitForSessionRequest(BaseModel):
    session_id: str
    target_statuses: list[OrchestrationStatus]
    timeout_ms: int = 120_000


class CancelSessionRequest(BaseModel):
    session_id: str
    force: bool = False
    reason: str | None = None


class SessionSummary(BaseModel):
    id: str
    role: OrchestrationRole
    raw_session_type: str
    project_id: str
    task_id: str | None = None
    parent_session_id: str | None = None
    provider: str | None = None
    status: OrchestrationStatus
    raw_status: str
    name: str
    goal: str | None = None
    summary: str | None = None
    host: str | None = None
    created_at: str
    updated_at: str
    last_activity_at: str | None = None
    tmux_session: str | None = None
    tmux_pane: str | None = None
    verification: dict[str, bool] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperationAcceptedResponse(BaseModel):
    request_status: Literal["queued", "submitted", "confirmed", "blocked", "failed"]
    operation_id: str
    session: SessionSummary | None = None
    delivery_state: Literal["queued", "submitted", "confirmed", "blocked", "failed"] = "queued"
    message: str | None = None
    recovery: str | None = None
    delivery: dict[str, Any] | None = None


class SessionEvent(BaseModel):
    id: str
    session_id: str
    event_type: str
    created_at: str
    data: dict[str, Any] = Field(default_factory=dict)


class ListOperationsRequest(BaseModel):
    operation_type: str | None = None
    session_id: str | None = None
    limit: int | None = None


class OperationRecord(BaseModel):
    operation_id: str
    operation_type: str
    session_id: str | None = None
    status: str
    request_payload: dict[str, Any]
    response_payload: dict[str, Any] | None = None
    created_at: str
    updated_at: str


class SessionEventRecord(BaseModel):
    id: str
    level: str
    category: str
    message: str
    created_at: str
    detail: dict[str, Any] | None = None
    project_id: str | None = None
    session_id: str | None = None

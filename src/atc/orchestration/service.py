from __future__ import annotations

import asyncio
from typing import Any

from atc.orchestration.errors import OrchestrationErrorCode, OrchestrationException
from atc.orchestration.models import (
    ListSessionsRequest,
    OperationAcceptedResponse,
    OrchestrationRole,
    OrchestrationStatus,
    SendInstructionRequest,
    SessionSummary,
    SpawnLeaderRequest,
    WaitForSessionRequest,
    normalize_role,
    normalize_status,
)
from atc.session.ace import _send_session_instruction
from atc.state import db as db_ops
from atc.tower.controller import BudgetConstrainedError, TowerBusyError, TowerController


class OrchestrationService:
    def __init__(
        self,
        db: Any,
        *,
        tower_controller: TowerController | None = None,
    ) -> None:
        self._db = db
        self._tower_controller = tower_controller

    async def get_session(self, session_id: str) -> SessionSummary:
        session = await db_ops.get_session(self._db, session_id)
        if session is None:
            raise OrchestrationException(
                OrchestrationErrorCode.SESSION_NOT_FOUND,
                f"Session {session_id} not found",
            )
        return await self._build_session_summary(session)

    async def list_sessions(self, request: ListSessionsRequest | None = None) -> list[SessionSummary]:
        request = request or ListSessionsRequest()
        raw_session_type = self._session_type_for_role(request.role)
        sessions = await db_ops.list_sessions(
            self._db,
            project_id=request.project_id,
            session_type=raw_session_type,
        )

        summaries = [await self._build_session_summary(session) for session in sessions]

        if request.status_in:
            allowed = set(request.status_in)
            summaries = [summary for summary in summaries if summary.status in allowed]

        if request.active_only:
            terminal = {OrchestrationStatus.STOPPED, OrchestrationStatus.FAILED}
            summaries = [summary for summary in summaries if summary.status not in terminal]

        if request.limit is not None:
            summaries = summaries[: request.limit]

        return summaries

    async def spawn_leader(self, request: SpawnLeaderRequest) -> OperationAcceptedResponse:
        project = await db_ops.get_project(self._db, request.project_id)
        if project is None:
            raise OrchestrationException(
                OrchestrationErrorCode.PROJECT_NOT_FOUND,
                f"Project {request.project_id} not found",
            )

        if self._tower_controller is None:
            raise OrchestrationException(
                OrchestrationErrorCode.PROVIDER_UNAVAILABLE,
                "Tower controller not available for leader orchestration",
                retryable=True,
            )

        try:
            result = await self._tower_controller.submit_goal(request.project_id, request.goal)
        except BudgetConstrainedError as exc:
            raise OrchestrationException(
                OrchestrationErrorCode.BUDGET_BLOCKED,
                str(exc),
            ) from exc
        except TowerBusyError as exc:
            code = (
                OrchestrationErrorCode.CONCURRENCY_LIMIT_REACHED
                if getattr(exc, "detail", None) == "at capacity"
                else OrchestrationErrorCode.SESSION_NOT_READY
            )
            raise OrchestrationException(code, str(exc), retryable=True) from exc

        leader_session_id = result.get("leader_session_id")
        if not leader_session_id:
            raise OrchestrationException(
                OrchestrationErrorCode.INTERNAL_STORAGE_ERROR,
                "Tower goal submission did not return a leader session id",
            )

        summary = await self.get_session(leader_session_id)
        return OperationAcceptedResponse(
            request_status="accepted",
            operation_id=request.idempotency_key,
            session=summary,
        )

    async def send_instruction(self, request: SendInstructionRequest) -> OperationAcceptedResponse:
        session = await db_ops.get_session(self._db, request.session_id)
        if session is None:
            raise OrchestrationException(
                OrchestrationErrorCode.SESSION_NOT_FOUND,
                f"Session {request.session_id} not found",
            )

        try:
            delivered = await _send_session_instruction(
                self._db,
                request.session_id,
                request.instruction,
            )
        except ValueError as exc:
            raise OrchestrationException(
                OrchestrationErrorCode.SESSION_NOT_READY,
                str(exc),
                retryable=True,
            ) from exc
        except Exception as exc:
            raise OrchestrationException(
                OrchestrationErrorCode.INTERNAL_STORAGE_ERROR,
                f"Instruction delivery failed for session {request.session_id}",
            ) from exc

        if not delivered:
            raise OrchestrationException(
                OrchestrationErrorCode.SESSION_NOT_READY,
                f"Instruction delivery was not accepted for session {request.session_id}",
                retryable=True,
            )

        summary = await self.get_session(request.session_id)
        return OperationAcceptedResponse(
            request_status="accepted",
            operation_id=request.idempotency_key,
            session=summary,
        )

    async def wait_for_session(self, request: WaitForSessionRequest) -> SessionSummary:
        timeout_s = max(request.timeout_ms, 0) / 1000
        deadline = asyncio.get_running_loop().time() + timeout_s
        target_statuses = set(request.target_statuses)

        while True:
            summary = await self.get_session(request.session_id)
            if summary.status in target_statuses:
                return summary
            if asyncio.get_running_loop().time() >= deadline:
                wanted = ", ".join(status.value for status in request.target_statuses)
                raise OrchestrationException(
                    OrchestrationErrorCode.SESSION_NOT_READY,
                    f"Session {request.session_id} did not reach target statuses [{wanted}] before timeout",
                    retryable=True,
                )
            await asyncio.sleep(0.5)

    async def _build_session_summary(self, session: Any) -> SessionSummary:
        role = normalize_role(session.session_type)
        status = normalize_status(session.status)
        goal = None
        metadata: dict[str, Any] = {}

        if role == OrchestrationRole.LEADER:
            leader = await db_ops.get_leader_by_project(self._db, session.project_id)
            if leader and leader.session_id == session.id:
                goal = leader.goal
                metadata["leader_status"] = leader.status

        project = await db_ops.get_project(self._db, session.project_id)
        provider = project.agent_provider if project else None

        return SessionSummary(
            id=session.id,
            role=role,
            raw_session_type=session.session_type,
            project_id=session.project_id,
            task_id=session.task_id,
            provider=provider,
            status=status,
            raw_status=session.status,
            name=session.name,
            goal=goal,
            host=session.host,
            created_at=session.created_at,
            updated_at=session.updated_at,
            tmux_session=session.tmux_session,
            tmux_pane=session.tmux_pane,
            metadata=metadata,
        )

    @staticmethod
    def _session_type_for_role(role: OrchestrationRole | None) -> str | None:
        if role is None:
            return None
        if role == OrchestrationRole.TOWER:
            return "tower"
        if role == OrchestrationRole.LEADER:
            return "manager"
        if role == OrchestrationRole.ACE:
            return "ace"
        return None

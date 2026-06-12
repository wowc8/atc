from __future__ import annotations

import asyncio
import json
from typing import Any

from atc.leader import leader as leader_ops
from atc.orchestration.errors import OrchestrationErrorCode, OrchestrationException
from atc.orchestration.models import (
    CancelSessionRequest,
    ListOperationsRequest,
    ListSessionsRequest,
    OperationAcceptedResponse,
    OperationRecord,
    OrchestrationRole,
    OrchestrationStatus,
    SendInstructionRequest,
    SessionEventRecord,
    SessionSummary,
    SpawnAceRequest,
    SpawnLeaderRequest,
    WaitForSessionRequest,
    normalize_role,
    normalize_status,
)
from atc.session import ace as ace_ops
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

    def _operation_response_from_payload(
        self, payload: dict[str, Any]
    ) -> OperationAcceptedResponse:
        """Validate stored operation payloads while accepting legacy optimistic status."""

        normalized = dict(payload)
        if normalized.get("request_status") == "accepted":
            normalized["request_status"] = normalized.get("delivery_state") or "queued"
        if normalized.get("delivery_state") == "accepted":
            normalized["delivery_state"] = "queued"
        return OperationAcceptedResponse.model_validate(normalized)

    async def list_operations(
        self, request: ListOperationsRequest | None = None
    ) -> list[OperationRecord]:
        request = request or ListOperationsRequest()
        ops = await db_ops.list_orchestration_operations(
            self._db,
            operation_type=request.operation_type,
            session_id=request.session_id,
            limit=request.limit,
        )
        return [
            OperationRecord(
                operation_id=op.operation_id,
                operation_type=op.operation_type,
                session_id=op.session_id,
                status=op.status,
                request_payload=json.loads(op.request_payload),
                response_payload=(json.loads(op.response_payload) if op.response_payload else None),
                created_at=op.created_at,
                updated_at=op.updated_at,
            )
            for op in ops
        ]

    async def get_operation(self, operation_id: str) -> OperationRecord:
        op = await db_ops.get_orchestration_operation(self._db, operation_id)
        if op is None:
            raise OrchestrationException(
                OrchestrationErrorCode.SESSION_NOT_FOUND,
                f"Operation {operation_id} not found",
            )
        return OperationRecord(
            operation_id=op.operation_id,
            operation_type=op.operation_type,
            session_id=op.session_id,
            status=op.status,
            request_payload=json.loads(op.request_payload),
            response_payload=(json.loads(op.response_payload) if op.response_payload else None),
            created_at=op.created_at,
            updated_at=op.updated_at,
        )

    async def list_session_events(
        self, session_id: str, *, limit: int | None = None
    ) -> list[SessionEventRecord]:
        events = await db_ops.list_app_events(self._db, session_id=session_id, limit=limit)
        return [
            SessionEventRecord(
                id=event.id,
                level=event.level,
                category=event.category,
                message=event.message,
                created_at=event.created_at,
                detail=(json.loads(event.detail) if event.detail else None),
                project_id=event.project_id,
                session_id=event.session_id,
            )
            for event in events
        ]

    async def get_session(self, session_id: str) -> SessionSummary:
        session = await db_ops.get_session(self._db, session_id)
        if session is None:
            raise OrchestrationException(
                OrchestrationErrorCode.SESSION_NOT_FOUND,
                f"Session {session_id} not found",
            )
        return await self._build_session_summary(session)

    async def list_sessions(
        self, request: ListSessionsRequest | None = None
    ) -> list[SessionSummary]:
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

        existing = await db_ops.get_orchestration_operation(self._db, request.idempotency_key)
        if existing is not None:
            if existing.operation_type != "spawn_leader":
                raise OrchestrationException(
                    OrchestrationErrorCode.IDEMPOTENCY_CONFLICT,
                    (
                        f"Operation id {request.idempotency_key} already used for "
                        f"{existing.operation_type}"
                    ),
                )
            if existing.response_payload:
                payload = json.loads(existing.response_payload)
                return self._operation_response_from_payload(payload)

        await db_ops.create_orchestration_operation(
            self._db,
            request.idempotency_key,
            "spawn_leader",
            request.model_dump_json(),
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
        response = OperationAcceptedResponse(
            request_status="queued",
            delivery_state="queued",
            operation_id=request.idempotency_key,
            session=summary,
            message=(
                "Leader spawn request accepted for orchestration; use session health "
                "or wait_for_session for runtime confirmation."
            ),
            recovery="queued is not proof that the Leader acted on the goal",
        )
        await db_ops.update_orchestration_operation(
            self._db,
            request.idempotency_key,
            session_id=leader_session_id,
            status=response.request_status,
            response_payload=response.model_dump_json(),
        )
        return response

    async def spawn_ace(self, request: SpawnAceRequest) -> OperationAcceptedResponse:
        project = await db_ops.get_project(self._db, request.project_id)
        if project is None:
            raise OrchestrationException(
                OrchestrationErrorCode.PROJECT_NOT_FOUND,
                f"Project {request.project_id} not found",
            )

        existing = await db_ops.get_orchestration_operation(self._db, request.idempotency_key)
        if existing is not None:
            if existing.operation_type != "spawn_ace":
                raise OrchestrationException(
                    OrchestrationErrorCode.IDEMPOTENCY_CONFLICT,
                    (
                        f"Operation id {request.idempotency_key} already used for "
                        f"{existing.operation_type}"
                    ),
                )
            if existing.response_payload:
                payload = json.loads(existing.response_payload)
                return self._operation_response_from_payload(payload)

        await db_ops.create_orchestration_operation(
            self._db,
            request.idempotency_key,
            "spawn_ace",
            request.model_dump_json(),
        )

        ace_name = request.context.get("task_title") if request.context else None
        ace_name = ace_name or (
            f"ace-{request.task_id[:8]}" if request.task_id else "ace-orchestration"
        )

        try:
            session_id = await ace_ops.create_ace(
                self._db,
                request.project_id,
                ace_name,
                task_id=request.task_id,
                host=request.host,
                working_dir=project.repo_path,
                deploy_spec_kwargs={
                    "project_name": project.name,
                    "task_title": (request.context or {}).get("task_title") or ace_name,
                    "task_description": request.instruction,
                    "project_id": request.project_id,
                    "repo_path": project.repo_path,
                    "github_repo": project.github_repo,
                    "context_entries": (request.context or {}).get("context_entries", []),
                },
            )
        except Exception as exc:
            raise OrchestrationException(
                OrchestrationErrorCode.INTERNAL_STORAGE_ERROR,
                f"Failed to spawn ace for project {request.project_id}",
            ) from exc

        if request.task_id:
            try:
                await db_ops.assign_task(
                    self._db,
                    request.task_id,
                    session_id,
                    request.idempotency_key,
                )
            except ValueError as exc:
                raise OrchestrationException(
                    OrchestrationErrorCode.INVALID_PARENT_RELATION,
                    str(exc),
                ) from exc

        delivery = None
        if request.instruction:
            delivery = await _send_session_instruction(self._db, session_id, request.instruction)
            if not delivery.ok:
                state = "blocked" if delivery.status == "blocked" else "failed"
                summary = await self.get_session(session_id)
                response = OperationAcceptedResponse(
                    request_status=state,
                    delivery_state=state,
                    operation_id=request.idempotency_key,
                    session=summary,
                    message=delivery.message
                    or f"Ace {session_id} was created but initial instruction delivery was {state}",
                    recovery="inspect Ace health and delivery trace before retrying",
                    delivery=delivery.as_dict(),
                )
                await db_ops.update_orchestration_operation(
                    self._db,
                    request.idempotency_key,
                    session_id=session_id,
                    status=state,
                    response_payload=response.model_dump_json(),
                )
                return response

        summary = await self.get_session(session_id)
        response = OperationAcceptedResponse(
            request_status="submitted" if delivery is not None else "queued",
            delivery_state="submitted" if delivery is not None else "queued",
            operation_id=request.idempotency_key,
            session=summary,
            message=(
                "Ace initial instruction submitted; provider acknowledgement is not verified"
                if delivery is not None
                else (
                    "Ace spawn request accepted for orchestration; "
                    "no instruction delivery was requested"
                )
            ),
            recovery="inspect Ace health and delivery trace for confirmation",
            delivery=delivery.as_dict() if delivery is not None else None,
        )
        await db_ops.update_orchestration_operation(
            self._db,
            request.idempotency_key,
            session_id=session_id,
            status=response.request_status,
            response_payload=response.model_dump_json(),
        )
        return response

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

        if not delivered.ok:
            state = "blocked" if delivered.status == "blocked" else "failed"
            summary = await self.get_session(request.session_id)
            return OperationAcceptedResponse(
                request_status=state,
                delivery_state=state,
                operation_id=request.idempotency_key,
                session=summary,
                message=delivered.message
                or f"Instruction delivery was {state} for session {request.session_id}",
                recovery="inspect session health and delivery traces before retrying",
                delivery=delivered.as_dict(),
            )

        summary = await self.get_session(request.session_id)
        return OperationAcceptedResponse(
            request_status="submitted",
            delivery_state="submitted",
            operation_id=request.idempotency_key,
            session=summary,
            message="Instruction submitted; provider acknowledgement is not verified",
            recovery="inspect session health and delivery traces for confirmation",
            delivery=delivered.as_dict(),
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
                    (
                        f"Session {request.session_id} did not reach target statuses "
                        f"[{wanted}] before timeout"
                    ),
                    retryable=True,
                )
            await asyncio.sleep(0.5)

    async def cancel_session(self, request: CancelSessionRequest) -> SessionSummary | None:
        session = await db_ops.get_session(self._db, request.session_id)
        if session is None:
            raise OrchestrationException(
                OrchestrationErrorCode.SESSION_NOT_FOUND,
                f"Session {request.session_id} not found",
            )

        role = normalize_role(session.session_type)

        if role == OrchestrationRole.ACE:
            if request.force:
                await ace_ops.destroy_ace(self._db, session.id)
                return None
            await ace_ops.stop_ace(self._db, session.id)
            return await self.get_session(session.id)

        if role == OrchestrationRole.LEADER:
            await leader_ops.stop_leader(self._db, session.project_id)
            return await self.get_session(session.id)

        if role == OrchestrationRole.TOWER:
            raise OrchestrationException(
                OrchestrationErrorCode.INVALID_ROLE,
                "Tower session cancellation is not yet supported through the orchestration surface",
            )

        raise OrchestrationException(
            OrchestrationErrorCode.INVALID_ROLE,
            f"Unsupported role for cancellation: {role.value}",
        )

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

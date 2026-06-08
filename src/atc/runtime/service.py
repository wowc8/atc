"""Central provider-neutral runtime service for Tower, Leader, and Ace flows."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from atc.providers.registry import create_provider_runtime, runtime_kwargs_for_provider
from atc.runtime.errors import RuntimeInvocationError
from atc.runtime.models import (
    InstructionRequest,
    ReadinessResult,
    ReadinessState,
    RoleKind,
    RuntimeBlockReason,
    RuntimeDeliveryResult,
    RuntimeInspection,
    RuntimeSessionHandle,
    RuntimeTransport,
    StartRoleRequest,
    StopRoleRequest,
    TaskAssignmentRequest,
)
from atc.runtime.tracing import (
    DeliveryAction,
    DeliveryReasonCode,
    DeliveryStage,
    DeliveryVerdict,
    append_trace_event,
    new_trace_id,
    trace_event,
)
from atc.state.db import get_connection_app_state

_STARTUP_INITIAL_DELAY_SECONDS = 2.0
_STARTUP_RESOLVE_DELAY_SECONDS = 1.0
_STARTUP_FINAL_DELAY_SECONDS = 1.0
_STARTUP_AUTO_RESOLVE_REASONS = {RuntimeBlockReason.TRUST}

if TYPE_CHECKING:
    from atc.providers.base import ProviderRuntime


class RuntimeService:
    """Provider-neutral entrypoint for ATC runtime operations.

    This service is the intended choke point for role session lifecycle,
    instruction delivery, task assignment, readiness checks, and restore.
    """

    def __init__(self) -> None:
        self._handles: dict[str, RuntimeSessionHandle] = {}
        self._providers: dict[str, ProviderRuntime] = {}

    def _get_provider_runtime(
        self, provider_name: str, conn: object | None = None
    ) -> ProviderRuntime:
        """Resolve a provider runtime, refreshing cached instances when live settings differ."""

        app_state = get_connection_app_state(conn) if conn is not None else None
        if app_state is None:
            app_state = getattr(getattr(conn, "_connection", None), "app_state", None)
        settings = getattr(app_state, "settings", None) if app_state is not None else None
        desired_kwargs = (
            runtime_kwargs_for_provider(provider_name, settings)
            if settings is not None
            else runtime_kwargs_for_provider(provider_name)
        )

        provider = self._providers.get(provider_name)
        if provider is None or any(
            getattr(provider, key, None) != value for key, value in desired_kwargs.items()
        ):
            provider = create_provider_runtime(provider_name, **desired_kwargs)
            self._providers[provider_name] = provider
        return provider

    def get_provider(self, provider_name: str) -> ProviderRuntime:
        """Resolve a provider runtime implementation by name."""

        return self._get_provider_runtime(provider_name)

    def remember_handle(self, handle: RuntimeSessionHandle) -> RuntimeSessionHandle:
        """Store a runtime handle for later session-targeted operations."""

        self._handles[handle.session_id] = handle
        return handle

    def get_handle(self, session_id: str) -> RuntimeSessionHandle:
        """Look up a previously remembered runtime handle."""

        try:
            return self._handles[session_id]
        except KeyError as exc:
            raise KeyError(f"Unknown runtime session handle: {session_id}") from exc

    def handle_from_session_record(self, session: object) -> RuntimeSessionHandle:
        """Build and remember a provider-neutral handle from a DB session row/model."""

        session_type = str(getattr(session, "session_type", "ace") or "ace")
        if session_type == "tower":
            role = RoleKind.TOWER
        elif session_type in {"leader", "manager"}:
            role = RoleKind.LEADER
        else:
            role = RoleKind.ACE
        handle = RuntimeSessionHandle(
            session_id=str(session.id),
            provider_name=str(getattr(session, "provider", None) or "claude_code"),
            role=role,
            transport=RuntimeTransport.TMUX,
            project_id=getattr(session, "project_id", None),
            tmux_session=getattr(session, "tmux_session", None),
            tmux_pane=getattr(session, "tmux_pane", None),
        )
        return self.remember_handle(handle)

    async def prepare_workspace(self, request: StartRoleRequest) -> None:
        """Run provider-owned workspace/bootstrap preparation for a role request."""

        provider = self.get_provider(request.provider_name)
        await provider.prepare_workspace(request)

    async def start_tower(self, request: StartRoleRequest) -> RuntimeSessionHandle:
        if request.role is not RoleKind.TOWER:
            raise ValueError("start_tower requires role=tower")
        return await self._start_role(request)

    async def stop_tower(
        self,
        handle: RuntimeSessionHandle,
        request: StopRoleRequest | None = None,
    ) -> None:
        if handle.role is not RoleKind.TOWER:
            raise ValueError("stop_tower requires tower handle")
        await self._stop_role(handle, request)

    async def start_leader(self, request: StartRoleRequest) -> RuntimeSessionHandle:
        if request.role is not RoleKind.LEADER:
            raise ValueError("start_leader requires role=leader")
        return await self._start_role(request)

    async def stop_leader(
        self,
        handle: RuntimeSessionHandle,
        request: StopRoleRequest | None = None,
    ) -> None:
        if handle.role is not RoleKind.LEADER:
            raise ValueError("stop_leader requires leader handle")
        await self._stop_role(handle, request)

    async def start_ace(self, request: StartRoleRequest) -> RuntimeSessionHandle:
        if request.role is not RoleKind.ACE:
            raise ValueError("start_ace requires role=ace")
        return await self._start_role(request)

    async def stop_ace(
        self,
        handle: RuntimeSessionHandle,
        request: StopRoleRequest | None = None,
    ) -> None:
        if handle.role is not RoleKind.ACE:
            raise ValueError("stop_ace requires ace handle")
        await self._stop_role(handle, request)

    async def spawn_existing_session(self, request: StartRoleRequest) -> RuntimeSessionHandle:
        """Materialize an already-created DB session row into a live runtime session."""

        trace_id = self._ensure_trace_id(request.metadata)
        self._append_start_event(
            request,
            trace_id,
            DeliveryStage.QUEUED,
            DeliveryVerdict.PENDING,
            DeliveryReasonCode.QUEUED,
        )
        self._append_start_event(
            request,
            trace_id,
            DeliveryStage.SPAWN_STARTED,
            DeliveryVerdict.PENDING,
            DeliveryReasonCode.SPAWN_REQUESTED,
        )
        provider = self._get_provider_runtime(
            request.provider_name, getattr(request, "connection", None)
        )
        try:
            handle = await provider.spawn_existing_session(request)
        except Exception as exc:
            self._append_start_event(
                request,
                trace_id,
                DeliveryStage.FAILED,
                DeliveryVerdict.FAILED,
                DeliveryReasonCode.PROVIDER_ERROR,
                details={"error_type": type(exc).__name__, "error": str(exc)},
            )
            raise
        self._append_spawned_event(request, handle, trace_id)
        handle = self.remember_handle(handle)
        await self._initialize_spawned_session(request, handle, trace_id, provider)
        return handle

    async def send_instruction(
        self,
        handle: RuntimeSessionHandle,
        request: InstructionRequest,
    ) -> RuntimeDeliveryResult:
        trace_id = self._ensure_trace_id(request.metadata)
        append_trace_event(
            request.metadata,
            trace_event(
                trace_id=trace_id,
                session_id=handle.session_id,
                role=handle.role.value,
                provider=handle.provider_name,
                pane_id=handle.tmux_pane,
                action=DeliveryAction.INSTRUCTION,
                stage=DeliveryStage.QUEUED,
                verdict=DeliveryVerdict.PENDING,
                reason_code=DeliveryReasonCode.QUEUED,
                details={"instruction_id": request.instruction_id},
            ),
        )
        provider = self.get_provider(handle.provider_name)
        try:
            await provider.send_instruction(handle, request)
        except Exception as exc:
            append_trace_event(
                request.metadata,
                trace_event(
                    trace_id=trace_id,
                    session_id=handle.session_id,
                    role=handle.role.value,
                    provider=handle.provider_name,
                    pane_id=handle.tmux_pane,
                    action=DeliveryAction.INSTRUCTION,
                    stage=DeliveryStage.FAILED,
                    verdict=DeliveryVerdict.FAILED,
                    reason_code=self._reason_code_for_delivery_exception(exc),
                    details={"error_type": type(exc).__name__, "error": str(exc)},
                ),
            )
            return self._result_from_metadata(
                handle, request.metadata, status="failed", message=str(exc)
            )
        self._append_provider_returned_event_if_needed(
            handle,
            request.metadata,
            trace_id,
            DeliveryAction.INSTRUCTION,
        )
        return self._result_from_metadata(handle, request.metadata)

    async def assign_project_to_leader(
        self,
        handle: RuntimeSessionHandle,
        request: InstructionRequest,
    ) -> RuntimeDeliveryResult:
        if handle.role is not RoleKind.LEADER:
            raise ValueError("assign_project_to_leader requires leader handle")
        return await self.send_instruction(handle, request)

    async def assign_task_to_ace(
        self,
        handle: RuntimeSessionHandle,
        request: TaskAssignmentRequest,
    ) -> RuntimeDeliveryResult:
        if handle.role is not RoleKind.ACE:
            raise ValueError("assign_task_to_ace requires ace handle")
        trace_id = self._ensure_trace_id(request.metadata)
        request.metadata["delivery_action"] = DeliveryAction.TASK_ASSIGNMENT.value
        append_trace_event(
            request.metadata,
            trace_event(
                trace_id=trace_id,
                session_id=handle.session_id,
                role=handle.role.value,
                provider=handle.provider_name,
                pane_id=handle.tmux_pane,
                action=DeliveryAction.TASK_ASSIGNMENT,
                stage=DeliveryStage.QUEUED,
                verdict=DeliveryVerdict.PENDING,
                reason_code=DeliveryReasonCode.QUEUED,
                details={
                    "task_id": request.task_id,
                    "assignment_id": request.assignment_id,
                },
            ),
        )
        provider = self.get_provider(handle.provider_name)
        try:
            await provider.assign_task(handle, request)
        except Exception as exc:
            append_trace_event(
                request.metadata,
                trace_event(
                    trace_id=trace_id,
                    session_id=handle.session_id,
                    role=handle.role.value,
                    provider=handle.provider_name,
                    pane_id=handle.tmux_pane,
                    action=DeliveryAction.TASK_ASSIGNMENT,
                    stage=DeliveryStage.FAILED,
                    verdict=DeliveryVerdict.FAILED,
                    reason_code=self._reason_code_for_delivery_exception(exc),
                    details={"error_type": type(exc).__name__, "error": str(exc)},
                ),
            )
            return self._result_from_metadata(
                handle, request.metadata, status="failed", message=str(exc)
            )
        else:
            self._append_provider_returned_event_if_needed(
                handle,
                request.metadata,
                trace_id,
                DeliveryAction.TASK_ASSIGNMENT,
            )
            return self._result_from_metadata(handle, request.metadata)
        finally:
            request.metadata.pop("delivery_action", None)

    async def check_readiness(self, handle: RuntimeSessionHandle) -> ReadinessResult:
        provider = self.get_provider(handle.provider_name)
        return await provider.check_readiness(handle)

    async def inspect_session(self, handle: RuntimeSessionHandle) -> RuntimeInspection:
        provider = self.get_provider(handle.provider_name)
        return await provider.inspect_session(handle)

    async def restore_session(self, handle: RuntimeSessionHandle) -> RuntimeInspection:
        provider = self.get_provider(handle.provider_name)
        return await provider.restore_session(handle)

    async def stop_session_record(
        self, session: object, request: StopRoleRequest | None = None
    ) -> None:
        """Stop a DB-backed session via the provider-neutral runtime boundary."""

        await self._stop_role(self.handle_from_session_record(session), request)

    async def inspect_session_record(self, session: object) -> RuntimeInspection:
        """Inspect a DB-backed session via the provider-neutral runtime boundary."""

        return await self.inspect_session(self.handle_from_session_record(session))

    async def _start_role(self, request: StartRoleRequest) -> RuntimeSessionHandle:
        provider = self.get_provider(request.provider_name)
        await provider.prepare_workspace(request)
        trace_id = self._ensure_trace_id(request.metadata)
        self._append_start_event(
            request,
            trace_id,
            DeliveryStage.QUEUED,
            DeliveryVerdict.PENDING,
            DeliveryReasonCode.QUEUED,
        )
        self._append_start_event(
            request,
            trace_id,
            DeliveryStage.SPAWN_STARTED,
            DeliveryVerdict.PENDING,
            DeliveryReasonCode.SPAWN_REQUESTED,
        )
        try:
            handle = await provider.start_role(request)
        except Exception as exc:
            self._append_start_event(
                request,
                trace_id,
                DeliveryStage.FAILED,
                DeliveryVerdict.FAILED,
                DeliveryReasonCode.PROVIDER_ERROR,
                details={"error_type": type(exc).__name__, "error": str(exc)},
            )
            raise
        self._append_spawned_event(request, handle, trace_id)
        handle = self.remember_handle(handle)
        await self._initialize_spawned_session(request, handle, trace_id, provider)
        return handle

    async def _initialize_spawned_session(
        self,
        request: StartRoleRequest,
        handle: RuntimeSessionHandle,
        trace_id: str,
        provider: ProviderRuntime,
    ) -> None:
        """Run the shared post-spawn handshake before any role receives work.

        Tower, Leader, and Ace panes all boot through provider TUIs. Give the
        TUI time to draw, inspect for startup blockers, auto-answer the narrow
        set of known safe prompts, then inspect again so callers do not send
        instructions into a trust/auth/permission question.
        """
        await asyncio.sleep(_STARTUP_INITIAL_DELAY_SECONDS)
        first = await provider.inspect_session(handle)
        self._append_startup_inspection_event(
            request,
            handle,
            trace_id,
            first,
            phase="initial",
        )
        if first.readiness is ReadinessState.BLOCKED:
            resolved = await self._try_resolve_startup_prompt(handle, first)
            if resolved:
                await asyncio.sleep(_STARTUP_RESOLVE_DELAY_SECONDS)
                second = await provider.inspect_session(handle)
                self._append_startup_inspection_event(
                    request,
                    handle,
                    trace_id,
                    second,
                    phase="after_auto_resolve",
                )
                await asyncio.sleep(_STARTUP_FINAL_DELAY_SECONDS)
                final = await provider.inspect_session(handle)
                self._append_startup_inspection_event(
                    request,
                    handle,
                    trace_id,
                    final,
                    phase="final",
                )
                return
        await asyncio.sleep(_STARTUP_FINAL_DELAY_SECONDS)
        final = await provider.inspect_session(handle)
        self._append_startup_inspection_event(
            request,
            handle,
            trace_id,
            final,
            phase="final",
        )

    @staticmethod
    async def _try_resolve_startup_prompt(
        handle: RuntimeSessionHandle,
        inspection: RuntimeInspection,
    ) -> bool:
        if inspection.block_reason not in _STARTUP_AUTO_RESOLVE_REASONS:
            return False
        if not handle.tmux_pane:
            return False
        from atc.runtime.tmux.substrate import run_tmux

        await run_tmux("send-keys", "-t", handle.tmux_pane, "Enter")
        return True

    @staticmethod
    def _append_startup_inspection_event(
        request: StartRoleRequest,
        handle: RuntimeSessionHandle,
        trace_id: str,
        inspection: RuntimeInspection,
        *,
        phase: str,
    ) -> None:
        if inspection.readiness is ReadinessState.BLOCKED:
            verdict = DeliveryVerdict.BLOCKED
            if inspection.block_reason is RuntimeBlockReason.TRUST:
                reason_code = DeliveryReasonCode.TRUST_REQUIRED
            elif inspection.block_reason in {RuntimeBlockReason.AUTH, RuntimeBlockReason.LOGIN}:
                reason_code = DeliveryReasonCode.AUTH_REQUIRED
            elif inspection.block_reason is RuntimeBlockReason.PERMISSION:
                reason_code = DeliveryReasonCode.PERMISSION_REQUIRED
            else:
                reason_code = DeliveryReasonCode.UNKNOWN_PROMPT_BLOCKER
        elif inspection.readiness is ReadinessState.ERROR:
            verdict = DeliveryVerdict.FAILED
            reason_code = DeliveryReasonCode.PROVIDER_ERROR
        elif inspection.readiness is ReadinessState.STOPPED:
            verdict = DeliveryVerdict.FAILED
            reason_code = DeliveryReasonCode.PANE_MISSING
        else:
            verdict = DeliveryVerdict.ACCEPTED
            reason_code = DeliveryReasonCode.SESSION_RUNNING
        append_trace_event(
            request.metadata,
            trace_event(
                trace_id=trace_id,
                session_id=handle.session_id,
                role=handle.role.value,
                provider=handle.provider_name,
                pane_id=handle.tmux_pane,
                action=DeliveryAction.SPAWN,
                stage=DeliveryStage.CONFIRMED_RUNNING
                if verdict is DeliveryVerdict.ACCEPTED
                else DeliveryStage.BLOCKED
                if verdict is DeliveryVerdict.BLOCKED
                else DeliveryStage.FAILED,
                verdict=verdict,
                reason_code=reason_code,
                prompt_state_after=inspection.readiness.value,
                first_output_excerpt=inspection.last_output_excerpt,
                details={
                    "startup_phase": phase,
                    "summary": inspection.summary,
                    "block_reason": inspection.block_reason.value
                    if inspection.block_reason is not None
                    else None,
                    **inspection.details,
                },
            ),
        )

    async def _stop_role(
        self,
        handle: RuntimeSessionHandle,
        request: StopRoleRequest | None = None,
    ) -> None:
        provider = self.get_provider(handle.provider_name)
        try:
            await provider.stop_role(handle, request)
        except RuntimeInvocationError as exc:
            message = str(exc).lower()
            if "can't find pane" in message or "no such pane" in message:
                return
            raise

    @staticmethod
    def _ensure_trace_id(metadata: dict[str, object]) -> str:
        existing = metadata.get("delivery_trace_id")
        if isinstance(existing, str) and existing:
            return existing
        trace_id = new_trace_id()
        metadata["delivery_trace_id"] = trace_id
        return trace_id

    @staticmethod
    def _append_start_event(
        request: StartRoleRequest,
        trace_id: str,
        stage: DeliveryStage,
        verdict: DeliveryVerdict,
        reason_code: DeliveryReasonCode,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        append_trace_event(
            request.metadata,
            trace_event(
                trace_id=trace_id,
                session_id=request.session_id,
                role=request.role.value,
                provider=request.provider_name,
                pane_id=None,
                action=DeliveryAction.SPAWN,
                stage=stage,
                verdict=verdict,
                reason_code=reason_code,
                details=details,
            ),
        )

    @staticmethod
    def _append_spawned_event(
        request: StartRoleRequest, handle: RuntimeSessionHandle, trace_id: str
    ) -> None:
        append_trace_event(
            request.metadata,
            trace_event(
                trace_id=trace_id,
                session_id=request.session_id,
                role=request.role.value,
                provider=request.provider_name,
                pane_id=handle.tmux_pane,
                action=DeliveryAction.SPAWN,
                stage=DeliveryStage.SPAWNED,
                verdict=DeliveryVerdict.ACCEPTED,
                reason_code=DeliveryReasonCode.PANE_SPAWNED,
                details={"tmux_session": handle.tmux_session},
            ),
        )

    @staticmethod
    def _append_provider_returned_event_if_needed(
        handle: RuntimeSessionHandle,
        metadata: dict[str, object],
        trace_id: str,
        action: DeliveryAction,
    ) -> None:
        events = metadata.get("delivery_trace_events")
        if isinstance(events, list) and events:
            latest = events[-1]
            if isinstance(latest, dict) and latest.get("stage") != DeliveryStage.QUEUED.value:
                return
        append_trace_event(
            metadata,
            trace_event(
                trace_id=trace_id,
                session_id=handle.session_id,
                role=handle.role.value,
                provider=handle.provider_name,
                pane_id=handle.tmux_pane,
                action=action,
                stage=DeliveryStage.CONFIRMED_RUNNING,
                verdict=DeliveryVerdict.ACCEPTED,
                reason_code=DeliveryReasonCode.DELIVERY_UNVERIFIED,
                details={"source": "provider_returned_without_detailed_trace"},
            ),
        )

    @staticmethod
    def _result_from_metadata(
        handle: RuntimeSessionHandle,
        metadata: dict[str, object],
        *,
        status: str | None = None,
        message: str | None = None,
    ) -> RuntimeDeliveryResult:
        events = metadata.get("delivery_trace_events")
        latest = events[-1] if isinstance(events, list) and events else {}
        verdict = str(latest.get("verdict", "")) if isinstance(latest, dict) else ""
        stage = str(latest.get("stage", "")) if isinstance(latest, dict) else ""
        if status is None:
            if verdict == DeliveryVerdict.BLOCKED.value:
                status = "blocked"
            elif verdict == DeliveryVerdict.FAILED.value:
                status = "failed"
            elif verdict == DeliveryVerdict.CONFIRMED.value:
                status = "confirmed"
            elif verdict == DeliveryVerdict.ACCEPTED.value:
                status = "delivered"
            else:
                status = "queued"
        details = latest.get("details", {}) if isinstance(latest, dict) else {}
        return RuntimeDeliveryResult(
            session_id=handle.session_id,
            provider_name=handle.provider_name,
            role=handle.role,
            status=status,
            stage=stage or None,
            verdict=verdict or None,
            reason_code=str(latest.get("reason_code"))
            if isinstance(latest, dict) and latest.get("reason_code")
            else None,
            trace_id=str(metadata.get("delivery_trace_id"))
            if metadata.get("delivery_trace_id")
            else None,
            message=message,
            details=details if isinstance(details, dict) else {},
        )

    @staticmethod
    def _reason_code_for_delivery_exception(exc: Exception) -> DeliveryReasonCode:
        message = str(exc).lower()
        if "pane" in message and ("missing" in message or "no tmux" in message):
            return DeliveryReasonCode.PANE_MISSING
        if "empty" in message:
            return DeliveryReasonCode.EMPTY_PAYLOAD
        if "auth" in message or "login" in message or "sign in" in message:
            return DeliveryReasonCode.AUTH_REQUIRED
        if "trust" in message:
            return DeliveryReasonCode.TRUST_REQUIRED
        if "permission" in message:
            return DeliveryReasonCode.PERMISSION_REQUIRED
        if "verif" in message:
            return DeliveryReasonCode.DELIVERY_UNVERIFIED
        return DeliveryReasonCode.UNKNOWN_ERROR

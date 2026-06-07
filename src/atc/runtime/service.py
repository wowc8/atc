"""Central provider-neutral runtime service for Tower, Leader, and Ace flows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from atc.providers.registry import create_provider_runtime, runtime_kwargs_for_provider
from atc.runtime.models import (
    InstructionRequest,
    ReadinessResult,
    RoleKind,
    RuntimeInspection,
    RuntimeSessionHandle,
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
        return self.remember_handle(handle)

    async def send_instruction(
        self,
        handle: RuntimeSessionHandle,
        request: InstructionRequest,
    ) -> None:
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
            raise

    async def assign_project_to_leader(
        self,
        handle: RuntimeSessionHandle,
        request: InstructionRequest,
    ) -> None:
        if handle.role is not RoleKind.LEADER:
            raise ValueError("assign_project_to_leader requires leader handle")
        await self.send_instruction(handle, request)

    async def assign_task_to_ace(
        self,
        handle: RuntimeSessionHandle,
        request: TaskAssignmentRequest,
    ) -> None:
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
            raise
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
        return self.remember_handle(handle)

    async def _stop_role(
        self,
        handle: RuntimeSessionHandle,
        request: StopRoleRequest | None = None,
    ) -> None:
        provider = self.get_provider(handle.provider_name)
        await provider.stop_role(handle, request)

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

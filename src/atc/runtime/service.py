"""Central provider-neutral runtime service for Tower, Leader, and Ace flows."""

from __future__ import annotations

from atc.providers.base import ProviderRuntime
from atc.providers.registry import create_provider_runtime
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


class RuntimeService:
    """Provider-neutral entrypoint for ATC runtime operations.

    This service is the intended choke point for role session lifecycle,
    instruction delivery, task assignment, readiness checks, and restore.
    """

    def __init__(self) -> None:
        self._handles: dict[str, RuntimeSessionHandle] = {}
        self._providers: dict[str, ProviderRuntime] = {}

    def get_provider(self, provider_name: str) -> ProviderRuntime:
        """Resolve a provider runtime implementation by name."""

        provider = self._providers.get(provider_name)
        if provider is None:
            provider = create_provider_runtime(provider_name)
            self._providers[provider_name] = provider
        return provider

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

        provider = self.get_provider(request.provider_name)
        handle = await provider.spawn_existing_session(request)
        return self.remember_handle(handle)

    async def send_instruction(
        self,
        handle: RuntimeSessionHandle,
        request: InstructionRequest,
    ) -> None:
        provider = self.get_provider(handle.provider_name)
        await provider.send_instruction(handle, request)

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
        provider = self.get_provider(handle.provider_name)
        await provider.assign_task(handle, request)

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
        handle = await provider.start_role(request)
        return self.remember_handle(handle)

    async def _stop_role(
        self,
        handle: RuntimeSessionHandle,
        request: StopRoleRequest | None = None,
    ) -> None:
        provider = self.get_provider(handle.provider_name)
        await provider.stop_role(handle, request)

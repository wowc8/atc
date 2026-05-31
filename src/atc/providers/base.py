"""Provider runtime contract for tmux-backed ATC provider implementations."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from atc.runtime.models import (
    InstructionRequest,
    ReadinessResult,
    RuntimeInspection,
    RuntimeSessionHandle,
    StartRoleRequest,
    StopRoleRequest,
    TaskAssignmentRequest,
)


@runtime_checkable
class ProviderRuntime(Protocol):
    """Contract that all ATC provider runtime implementations must satisfy."""

    provider_name: str

    async def prepare_workspace(self, request: StartRoleRequest) -> None:
        """Prepare working directory/bootstrap artifacts before session startup."""

    async def start_role(self, request: StartRoleRequest) -> RuntimeSessionHandle:
        """Start a provider-backed Tower, Leader, or Ace session."""

    async def stop_role(
        self,
        handle: RuntimeSessionHandle,
        request: StopRoleRequest | None = None,
    ) -> None:
        """Stop a provider-backed session."""

    async def send_instruction(
        self,
        handle: RuntimeSessionHandle,
        request: InstructionRequest,
    ) -> None:
        """Deliver a general instruction to an existing provider session."""

    async def assign_task(
        self,
        handle: RuntimeSessionHandle,
        request: TaskAssignmentRequest,
    ) -> None:
        """Deliver a task assignment to an existing provider session."""

    async def check_readiness(
        self,
        handle: RuntimeSessionHandle,
    ) -> ReadinessResult:
        """Return normalized readiness state for a provider session."""

    async def inspect_session(
        self,
        handle: RuntimeSessionHandle,
    ) -> RuntimeInspection:
        """Inspect health/output/readiness for a provider session."""

    async def restore_session(
        self,
        handle: RuntimeSessionHandle,
    ) -> RuntimeInspection:
        """Attempt to restore/reattach/validate an existing provider session."""

    async def spawn_existing_session(
        self,
        request: StartRoleRequest,
    ) -> RuntimeSessionHandle:
        """Spawn a runtime pane/process for an already-created ATC session row."""

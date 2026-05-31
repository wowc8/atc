"""Shared runtime/provider error types for ATC."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from atc.runtime.models import RuntimeBlockReason, WrapperExitCode


@dataclass(slots=True)
class RuntimeErrorContext:
    """Structured context for runtime/provider failures."""

    session_id: str | None = None
    provider_name: str | None = None
    command: str | None = None
    role: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class ATCRuntimeError(Exception):
    """Base error for runtime/provider layer failures."""

    def __init__(self, message: str, *, context: RuntimeErrorContext | None = None) -> None:
        super().__init__(message)
        self.context = context or RuntimeErrorContext()


class RuntimeBlockedError(ATCRuntimeError):
    """Raised when a runtime action is blocked by auth/trust/prompt conditions."""

    def __init__(
        self,
        message: str,
        *,
        reason: RuntimeBlockReason = RuntimeBlockReason.UNKNOWN,
        context: RuntimeErrorContext | None = None,
    ) -> None:
        super().__init__(message, context=context)
        self.reason = reason


class RuntimeNotReadyError(ATCRuntimeError):
    """Raised when a runtime action cannot proceed because the session is not ready."""


class RuntimeSessionMissingError(ATCRuntimeError):
    """Raised when a targeted runtime session or pane cannot be found."""


class RuntimeDeliveryError(ATCRuntimeError):
    """Raised when instruction or task delivery fails."""


class RuntimeRestoreError(ATCRuntimeError):
    """Raised when runtime restoration fails."""


class RuntimeInvocationError(ATCRuntimeError):
    """Raised when ATC invokes the wrapper or provider incorrectly."""


def map_wrapper_exit_code(
    exit_code: int,
    *,
    message: str,
    context: RuntimeErrorContext | None = None,
) -> ATCRuntimeError | None:
    """Map a wrapper exit code to a typed runtime error, if any."""

    if exit_code == WrapperExitCode.SUCCESS:
        return None
    if exit_code == WrapperExitCode.BLOCKED_AUTH:
        return RuntimeBlockedError(message, reason=RuntimeBlockReason.AUTH, context=context)
    if exit_code == WrapperExitCode.NOT_READY:
        return RuntimeNotReadyError(message, context=context)
    if exit_code == WrapperExitCode.SESSION_MISSING:
        return RuntimeSessionMissingError(message, context=context)
    if exit_code == WrapperExitCode.INVALID_ARGS:
        return RuntimeInvocationError(message, context=context)
    if exit_code == WrapperExitCode.DELIVERY_FAILED:
        return RuntimeDeliveryError(message, context=context)
    if exit_code == WrapperExitCode.RESTORE_FAILED:
        return RuntimeRestoreError(message, context=context)
    return ATCRuntimeError(message, context=context)

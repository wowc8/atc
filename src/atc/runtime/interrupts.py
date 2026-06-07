"""Provider-neutral runtime interrupt detection.

Phase 4 centralizes startup/trust/permission/welcome prompt handling as
structured runtime interrupts. Provider runtimes supply only trigger phrases;
the shared detector maps observed terminal text into stable interrupt types,
reason codes, and operator-facing actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from atc.runtime.models import ReadinessState, RuntimeBlockReason
from atc.runtime.tracing import DeliveryReasonCode


class RuntimeInterruptType(StrEnum):
    """Known runtime dialog/interruption classes."""

    TRUST_PROMPT = "trust_prompt"
    PERMISSION_PROMPT = "permission_prompt"
    LOGIN_REQUIRED = "login_required"
    WELCOME_SCREEN = "welcome_screen"
    PROVIDER_ERROR = "provider_error"
    UNKNOWN_PROMPT_BLOCKER = "unknown_prompt_blocker"


class RuntimeInterruptDisposition(StrEnum):
    """How the runtime should treat an observed interrupt."""

    BLOCKING = "blocking"
    INFORMATIONAL = "informational"


@dataclass(frozen=True, slots=True)
class RuntimeInterrupt:
    """Structured classification for a startup/runtime prompt blocker."""

    interrupt_type: RuntimeInterruptType
    disposition: RuntimeInterruptDisposition
    reason_code: DeliveryReasonCode
    readiness: ReadinessState
    block_reason: RuntimeBlockReason | None
    summary: str
    operator_action: str
    matched_trigger: str | None = None
    safe_to_auto_resolve: bool = False

    def to_trace_details(self) -> dict[str, object]:
        """Return stable metadata for delivery trace events."""
        return {
            "runtime_interrupt": self.interrupt_type.value,
            "interrupt_disposition": self.disposition.value,
            "operator_action": self.operator_action,
            "matched_trigger": self.matched_trigger,
            "safe_to_auto_resolve": self.safe_to_auto_resolve,
        }


@dataclass(frozen=True, slots=True)
class RuntimeInterruptSpec:
    """Provider-specific phrases consumed by the central interrupt detector."""

    trust_triggers: tuple[str, ...] = ()
    permission_triggers: tuple[str, ...] = ()
    login_triggers: tuple[str, ...] = ()
    welcome_triggers: tuple[str, ...] = ()
    provider_error_triggers: tuple[str, ...] = ()
    unknown_prompt_triggers: tuple[str, ...] = ()
    auto_resolvable_trust_triggers: tuple[str, ...] = field(default_factory=tuple)


def detect_runtime_interrupt(
    excerpt: str,
    spec: RuntimeInterruptSpec,
) -> RuntimeInterrupt | None:
    """Classify a terminal excerpt as a provider-neutral interrupt, if any."""
    lowered = excerpt.lower()

    match = _first_match(lowered, spec.trust_triggers)
    if match:
        return RuntimeInterrupt(
            interrupt_type=RuntimeInterruptType.TRUST_PROMPT,
            disposition=RuntimeInterruptDisposition.BLOCKING,
            reason_code=DeliveryReasonCode.TRUST_REQUIRED,
            readiness=ReadinessState.BLOCKED,
            block_reason=RuntimeBlockReason.TRUST,
            summary="Blocked on trust prompt",
            operator_action="resolve_trust",
            matched_trigger=match,
            safe_to_auto_resolve=match in spec.auto_resolvable_trust_triggers,
        )

    match = _first_match(lowered, spec.permission_triggers)
    if match:
        return RuntimeInterrupt(
            interrupt_type=RuntimeInterruptType.PERMISSION_PROMPT,
            disposition=RuntimeInterruptDisposition.BLOCKING,
            reason_code=DeliveryReasonCode.PERMISSION_REQUIRED,
            readiness=ReadinessState.BLOCKED,
            block_reason=RuntimeBlockReason.PERMISSION,
            summary="Blocked on permission prompt",
            operator_action="resolve_permission",
            matched_trigger=match,
        )

    match = _first_match(lowered, spec.login_triggers)
    if match:
        return RuntimeInterrupt(
            interrupt_type=RuntimeInterruptType.LOGIN_REQUIRED,
            disposition=RuntimeInterruptDisposition.BLOCKING,
            reason_code=DeliveryReasonCode.AUTH_REQUIRED,
            readiness=ReadinessState.BLOCKED,
            block_reason=RuntimeBlockReason.AUTH,
            summary="Blocked on authentication",
            operator_action="resolve_auth",
            matched_trigger=match,
        )

    match = _first_match(lowered, spec.provider_error_triggers)
    if match:
        return RuntimeInterrupt(
            interrupt_type=RuntimeInterruptType.PROVIDER_ERROR,
            disposition=RuntimeInterruptDisposition.BLOCKING,
            reason_code=DeliveryReasonCode.PROVIDER_ERROR,
            readiness=ReadinessState.ERROR,
            block_reason=RuntimeBlockReason.PROVIDER_PROMPT,
            summary="Blocked on provider error",
            operator_action="inspect_provider_error",
            matched_trigger=match,
        )

    match = _first_match(lowered, spec.unknown_prompt_triggers)
    if match:
        return RuntimeInterrupt(
            interrupt_type=RuntimeInterruptType.UNKNOWN_PROMPT_BLOCKER,
            disposition=RuntimeInterruptDisposition.BLOCKING,
            reason_code=DeliveryReasonCode.UNKNOWN_PROMPT_BLOCKER,
            readiness=ReadinessState.BLOCKED,
            block_reason=RuntimeBlockReason.UNKNOWN,
            summary="Blocked on unknown provider prompt",
            operator_action="inspect_prompt",
            matched_trigger=match,
        )

    match = _first_match(lowered, spec.welcome_triggers)
    if match:
        return RuntimeInterrupt(
            interrupt_type=RuntimeInterruptType.WELCOME_SCREEN,
            disposition=RuntimeInterruptDisposition.INFORMATIONAL,
            reason_code=DeliveryReasonCode.WELCOME_SCREEN,
            readiness=ReadinessState.BUSY,
            block_reason=None,
            summary="Provider welcome/startup screen visible",
            operator_action="wait_or_continue",
            matched_trigger=match,
        )

    return None


def interrupt_prompt_state(interrupt: RuntimeInterrupt | None, fallback: str) -> str:
    """Return provider-neutral prompt-state text for trace/readiness metadata."""
    if interrupt is None:
        return fallback
    if interrupt.block_reason is not None:
        return f"{interrupt.readiness.value}:{interrupt.block_reason.value}"
    return f"{interrupt.readiness.value}:{interrupt.interrupt_type.value}"


def _first_match(lowered_excerpt: str, triggers: tuple[str, ...]) -> str | None:
    for trigger in triggers:
        if trigger.lower() in lowered_excerpt:
            return trigger.lower()
    return None

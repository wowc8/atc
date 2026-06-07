from __future__ import annotations

from atc.runtime.interrupts import (
    RuntimeInterruptSpec,
    RuntimeInterruptType,
    detect_runtime_interrupt,
    interrupt_prompt_state,
)
from atc.runtime.models import ReadinessState, RuntimeBlockReason
from atc.runtime.tracing import DeliveryReasonCode


def test_detects_trust_prompt_as_blocking_interrupt() -> None:
    interrupt = detect_runtime_interrupt(
        "Do you trust this folder?",
        RuntimeInterruptSpec(trust_triggers=("trust this folder",)),
    )

    assert interrupt is not None
    assert interrupt.interrupt_type is RuntimeInterruptType.TRUST_PROMPT
    assert interrupt.reason_code is DeliveryReasonCode.TRUST_REQUIRED
    assert interrupt.readiness is ReadinessState.BLOCKED
    assert interrupt.block_reason is RuntimeBlockReason.TRUST
    assert interrupt.operator_action == "resolve_trust"


def test_detects_permission_prompt_with_stable_action() -> None:
    interrupt = detect_runtime_interrupt(
        "Allow this command to continue?",
        RuntimeInterruptSpec(permission_triggers=("allow this command",)),
    )

    assert interrupt is not None
    assert interrupt.interrupt_type is RuntimeInterruptType.PERMISSION_PROMPT
    assert interrupt.reason_code is DeliveryReasonCode.PERMISSION_REQUIRED
    assert interrupt.block_reason is RuntimeBlockReason.PERMISSION
    assert interrupt.operator_action == "resolve_permission"


def test_detects_welcome_screen_as_informational_busy_state() -> None:
    interrupt = detect_runtime_interrupt(
        "Welcome to Claude Code — tips for getting started",
        RuntimeInterruptSpec(welcome_triggers=("tips for getting started",)),
    )

    assert interrupt is not None
    assert interrupt.interrupt_type is RuntimeInterruptType.WELCOME_SCREEN
    assert interrupt.reason_code is DeliveryReasonCode.WELCOME_SCREEN
    assert interrupt.readiness is ReadinessState.BUSY
    assert interrupt.block_reason is None


def test_interrupt_prompt_state_uses_provider_neutral_block_reason() -> None:
    interrupt = detect_runtime_interrupt(
        "Permission required",
        RuntimeInterruptSpec(permission_triggers=("permission",)),
    )

    assert interrupt_prompt_state(interrupt, "ready") == "blocked:permission"

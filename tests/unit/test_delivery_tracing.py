from __future__ import annotations

import asyncio
from contextlib import suppress
from unittest.mock import AsyncMock, patch

from atc.providers.codex.runtime import CodexRuntime
from atc.runtime.models import InstructionRequest, RoleKind, RuntimeSessionHandle, RuntimeTransport
from atc.runtime.tracing import DeliveryReasonCode, DeliveryStage, DeliveryVerdict


def test_codex_instruction_emits_structured_delivery_trace_events() -> None:
    runtime = CodexRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-trace-1",
        provider_name="codex",
        role=RoleKind.ACE,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%42",
    )
    request = InstructionRequest(
        session_id="sess-trace-1",
        message="Do the traced work",
        metadata={"delivery_trace_id": "trace-1"},
    )

    with (
        patch("atc.runtime.tmux.runner.pane_exists", AsyncMock(return_value=True)),
        patch(
            "atc.runtime.tmux.runner.capture_pane_text",
            AsyncMock(side_effect=["ready\n>\n", "Thinking about traced work..."]),
        ),
        patch(
            "atc.runtime.tmux.runner.send_bracketed_instruction", AsyncMock()
        ) as send_instruction,
    ):
        asyncio.run(runtime.send_instruction(handle, request))

    send_instruction.assert_awaited_once_with("atc", "%42", "Do the traced work")
    events = request.metadata["delivery_trace_events"]
    assert [event["stage"] for event in events] == [
        DeliveryStage.WRITE_STARTED.value,
        DeliveryStage.WRITTEN_TO_PTY.value,
        DeliveryStage.SUBMIT_ATTEMPTED.value,
        DeliveryStage.AGENT_OUTPUT_OBSERVED.value,
    ]
    assert events[-1]["verdict"] == DeliveryVerdict.CONFIRMED.value
    assert events[-1]["reason_code"] == DeliveryReasonCode.AGENT_OUTPUT.value
    assert events[-1]["trace_id"] == "trace-1"
    assert events[-1]["session_id"] == "sess-trace-1"
    assert events[-1]["role"] == "ace"
    assert events[-1]["provider"] == "codex"
    assert events[-1]["pane_id"] == "%42"
    assert events[-1]["prompt_state_before"] == "ready"
    assert events[-1]["prompt_state_after"] == "busy"


def test_codex_instruction_failure_trace_has_stable_reason_code() -> None:
    runtime = CodexRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-trace-missing",
        provider_name="codex",
        role=RoleKind.LEADER,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%99",
    )
    request = InstructionRequest(
        session_id="sess-trace-missing",
        message="hello",
        metadata={"delivery_trace_id": "trace-missing"},
    )

    with (
        patch("atc.runtime.tmux.runner.pane_exists", AsyncMock(return_value=False)),
        suppress(Exception),
    ):
        asyncio.run(runtime.send_instruction(handle, request))

    events = request.metadata["delivery_trace_events"]
    assert events[-1]["stage"] == DeliveryStage.FAILED.value
    assert events[-1]["verdict"] == DeliveryVerdict.FAILED.value
    assert events[-1]["reason_code"] == DeliveryReasonCode.PANE_MISSING.value

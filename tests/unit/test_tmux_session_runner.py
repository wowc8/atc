from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from atc.runtime.errors import RuntimeDeliveryError, RuntimeSessionMissingError
from atc.runtime.models import RoleKind, RuntimeSessionHandle, RuntimeTransport
from atc.runtime.tmux.runner import RunnerTerminalVerdict, TmuxSessionRunner
from atc.runtime.tracing import (
    DeliveryAction,
    DeliveryReasonCode,
    DeliveryStage,
    DeliveryVerdict,
)


def _handle(pane: str | None = "%1") -> RuntimeSessionHandle:
    return RuntimeSessionHandle(
        session_id="sess-runner-1",
        provider_name="codex",
        role=RoleKind.ACE,
        transport=RuntimeTransport.TMUX,
        tmux_session="atc",
        tmux_pane=pane,
    )


def _terminal_verdict(_text: str, state: str | None, _output: str) -> RunnerTerminalVerdict:
    if state == "blocked:trust":
        return RunnerTerminalVerdict(
            stage=DeliveryStage.BLOCKED,
            verdict=DeliveryVerdict.BLOCKED,
            reason_code=DeliveryReasonCode.TRUST_REQUIRED,
        )
    return RunnerTerminalVerdict(
        stage=DeliveryStage.AGENT_OUTPUT_OBSERVED,
        verdict=DeliveryVerdict.CONFIRMED,
        reason_code=DeliveryReasonCode.AGENT_OUTPUT,
    )


def _runner() -> TmuxSessionRunner:
    return TmuxSessionRunner(
        tmux_session="atc",
        provider_name="codex",
        prompt_state_for_excerpt=lambda text: "blocked:trust" if "trust" in text else "ready",
        terminal_verdict_for_observation=_terminal_verdict,
    )


def test_tmux_session_runner_traces_write_submit_and_terminal_verdict() -> None:
    metadata = {"delivery_trace_id": "trace-1"}
    with (
        patch("atc.runtime.tmux.runner.pane_exists", AsyncMock(return_value=True)),
        patch(
            "atc.runtime.tmux.runner.capture_pane_text",
            AsyncMock(side_effect=["❯", "Do you trust the contents?"]),
        ),
        patch("atc.runtime.tmux.runner.send_bracketed_instruction", AsyncMock()) as send,
    ):
        result = asyncio.run(
            _runner().deliver_instruction(
                handle=_handle(),
                metadata=metadata,
                trace_id="trace-1",
                action=DeliveryAction.INSTRUCTION,
                payload_loader=AsyncMock(return_value="hello"),
            )
        )

    send.assert_awaited_once_with("atc", "%1", "hello")
    assert result.prompt_state_after == "blocked:trust"
    assert [event["stage"] for event in metadata["delivery_trace_events"]] == [
        "write_started",
        "written_to_pty",
        "submit_attempted",
        "blocked",
    ]
    assert metadata["delivery_trace_events"][-1]["reason_code"] == "trust_required"


def test_tmux_session_runner_traces_missing_pane() -> None:
    metadata = {"delivery_trace_id": "trace-2"}
    with pytest.raises(RuntimeSessionMissingError):
        asyncio.run(
            _runner().deliver_instruction(
                handle=_handle(pane=None),
                metadata=metadata,
                trace_id="trace-2",
                action=DeliveryAction.INSTRUCTION,
                payload_loader=AsyncMock(return_value="hello"),
            )
        )

    assert metadata["delivery_trace_events"][-1]["stage"] == "failed"
    assert metadata["delivery_trace_events"][-1]["reason_code"] == "pane_missing"


def test_tmux_session_runner_traces_empty_payload() -> None:
    metadata = {"delivery_trace_id": "trace-3"}
    with (
        patch("atc.runtime.tmux.runner.pane_exists", AsyncMock(return_value=True)),
        pytest.raises(RuntimeDeliveryError),
    ):
        asyncio.run(
            _runner().deliver_instruction(
                handle=_handle(),
                metadata=metadata,
                trace_id="trace-3",
                action=DeliveryAction.INSTRUCTION,
                payload_loader=AsyncMock(return_value=""),
            )
        )

    assert metadata["delivery_trace_events"][-1]["stage"] == "failed"
    assert metadata["delivery_trace_events"][-1]["reason_code"] == "empty_payload"

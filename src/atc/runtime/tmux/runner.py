"""Shared hardened tmux session runner for terminal-agent delivery.

Phase 2 introduces this runner as the authoritative place for pane inspection,
payload validation, atomic PTY writes, Enter submission, verification reads, and
provider-neutral delivery verdicts. Providers keep prompt classification and
configuration concerns behind small adapters.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from atc.runtime.errors import RuntimeDeliveryError, RuntimeSessionMissingError
from atc.runtime.tmux.control import send_bracketed_instruction
from atc.runtime.tmux.substrate import capture_pane_text, pane_exists
from atc.runtime.tracing import (
    DeliveryAction,
    DeliveryReasonCode,
    DeliveryStage,
    DeliveryVerdict,
    append_trace_event,
    trace_event,
)

PromptClassifier = Callable[[str], str]
TerminalClassifier = Callable[[str, str | None, str], "RunnerTerminalVerdict"]
PayloadLoader = Callable[[], Awaitable[str]]

if TYPE_CHECKING:
    from atc.runtime.models import RuntimeSessionHandle


@dataclass(frozen=True, slots=True)
class RunnerTerminalVerdict:
    """Provider adapter result for a post-delivery terminal observation."""

    stage: DeliveryStage
    verdict: DeliveryVerdict
    reason_code: DeliveryReasonCode
    raises: bool = False
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class RunnerDeliveryResult:
    """Final observation emitted by the runner."""

    before: str
    after: str
    prompt_state_before: str
    prompt_state_after: str
    terminal_verdict: RunnerTerminalVerdict


@dataclass(frozen=True, slots=True)
class TmuxSessionRunner:
    """Shared runner for tmux-backed instruction delivery."""

    tmux_session: str
    provider_name: str
    prompt_state_for_excerpt: PromptClassifier
    terminal_verdict_for_observation: TerminalClassifier
    observe_delay_seconds: float = 0.0
    before_capture_lines: int = 40
    after_capture_lines: int = 80

    async def deliver_instruction(
        self,
        *,
        handle: RuntimeSessionHandle,
        metadata: dict[str, Any],
        trace_id: str,
        action: DeliveryAction,
        payload_loader: PayloadLoader,
    ) -> RunnerDeliveryResult:
        """Validate, write, submit, verify, and trace one instruction delivery."""

        if not handle.tmux_pane:
            self._append_trace(
                handle=handle,
                metadata=metadata,
                trace_id=trace_id,
                action=action,
                stage=DeliveryStage.FAILED,
                verdict=DeliveryVerdict.FAILED,
                reason_code=DeliveryReasonCode.PANE_MISSING,
            )
            raise RuntimeSessionMissingError(
                f"{self.provider_name} session has no tmux pane recorded"
            )
        if not await pane_exists(handle.tmux_pane):
            self._append_trace(
                handle=handle,
                metadata=metadata,
                trace_id=trace_id,
                action=action,
                stage=DeliveryStage.FAILED,
                verdict=DeliveryVerdict.FAILED,
                reason_code=DeliveryReasonCode.PANE_MISSING,
            )
            raise RuntimeSessionMissingError(f"{self.provider_name} session pane is missing")

        text = await payload_loader()
        if not text:
            self._append_trace(
                handle=handle,
                metadata=metadata,
                trace_id=trace_id,
                action=action,
                stage=DeliveryStage.FAILED,
                verdict=DeliveryVerdict.FAILED,
                reason_code=DeliveryReasonCode.EMPTY_PAYLOAD,
            )
            raise RuntimeDeliveryError("Instruction payload was empty")

        before = await capture_pane_text(handle.tmux_pane, lines=self.before_capture_lines)
        before_state = self.prompt_state_for_excerpt(before)
        self._append_trace(
            handle=handle,
            metadata=metadata,
            trace_id=trace_id,
            action=action,
            stage=DeliveryStage.WRITE_STARTED,
            verdict=DeliveryVerdict.PENDING,
            reason_code=DeliveryReasonCode.PTY_WRITE_STARTED,
            prompt_state_before=before_state,
            first_output_excerpt=before,
        )

        await send_bracketed_instruction(self.tmux_session, handle.tmux_pane, text)
        if self.observe_delay_seconds > 0:
            await asyncio.sleep(self.observe_delay_seconds)

        after = await capture_pane_text(handle.tmux_pane, lines=self.after_capture_lines)
        after_state = self.prompt_state_for_excerpt(after)
        self._append_trace(
            handle=handle,
            metadata=metadata,
            trace_id=trace_id,
            action=action,
            stage=DeliveryStage.WRITTEN_TO_PTY,
            verdict=DeliveryVerdict.ACCEPTED,
            reason_code=DeliveryReasonCode.PTY_WRITE_ACCEPTED,
            prompt_state_before=before_state,
            prompt_state_after=after_state,
            first_output_excerpt=after,
        )
        self._append_trace(
            handle=handle,
            metadata=metadata,
            trace_id=trace_id,
            action=action,
            stage=DeliveryStage.SUBMIT_ATTEMPTED,
            verdict=DeliveryVerdict.ACCEPTED,
            reason_code=DeliveryReasonCode.SUBMIT_SENT,
            prompt_state_before=before_state,
            prompt_state_after=after_state,
        )

        terminal_verdict = self.terminal_verdict_for_observation(text, after_state, after)
        self._append_trace(
            handle=handle,
            metadata=metadata,
            trace_id=trace_id,
            action=action,
            stage=terminal_verdict.stage,
            verdict=terminal_verdict.verdict,
            reason_code=terminal_verdict.reason_code,
            prompt_state_before=before_state,
            prompt_state_after=after_state,
            first_output_excerpt=after,
        )
        if terminal_verdict.raises:
            raise RuntimeDeliveryError(
                terminal_verdict.error_message or "Instruction delivery could not be verified"
            )
        return RunnerDeliveryResult(
            before=before,
            after=after,
            prompt_state_before=before_state,
            prompt_state_after=after_state,
            terminal_verdict=terminal_verdict,
        )

    def _append_trace(
        self,
        *,
        handle: RuntimeSessionHandle,
        metadata: dict[str, Any],
        trace_id: str,
        action: DeliveryAction,
        stage: DeliveryStage,
        verdict: DeliveryVerdict,
        reason_code: DeliveryReasonCode,
        prompt_state_before: str | None = None,
        prompt_state_after: str | None = None,
        first_output_excerpt: str | None = None,
    ) -> None:
        if not trace_id:
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
                stage=stage,
                verdict=verdict,
                reason_code=reason_code,
                prompt_state_before=prompt_state_before,
                prompt_state_after=prompt_state_after,
                first_output_excerpt=first_output_excerpt,
            ),
        )

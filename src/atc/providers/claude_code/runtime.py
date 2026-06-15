"""First Claude Code provider runtime implementation on the new contract."""

from __future__ import annotations

import asyncio
import re

from atc.providers.base import ProviderRuntime
from atc.runtime.errors import RuntimeDeliveryError, RuntimeSessionMissingError
from atc.runtime.interrupts import (
    RuntimeInterruptSpec,
    detect_runtime_interrupt,
    interrupt_prompt_state,
)
from atc.runtime.models import (
    InstructionRequest,
    ReadinessResult,
    ReadinessState,
    RuntimeBlockReason,
    RuntimeInspection,
    RuntimeSessionHandle,
    RuntimeTransport,
    StartRoleRequest,
    StopRoleRequest,
    TaskAssignmentRequest,
)
from atc.runtime.tmux.control import send_bracketed_instruction
from atc.runtime.tmux.substrate import (
    build_path_env_prefix,
    capture_pane_text,
    ensure_tmux_session,
    kill_pane,
    pane_exists,
    run_tmux,
    spawn_window_pane,
)
from atc.runtime.tracing import (
    DeliveryAction,
    DeliveryReasonCode,
    DeliveryStage,
    DeliveryVerdict,
    append_trace_event,
    trace_event,
)

_BARE_PROMPT_RE = re.compile(r"(^|\n)\s*❯\s*$", re.MULTILINE)
_WELCOME_TRIGGERS = (
    "tips for getting started",
    "welcome to claude code",
)
_TRUST_TRIGGERS = (
    "trust this folder",
    "do you trust",
    "bypass permissions",
)
_PERMISSION_TRIGGERS = (
    "permission",
    "allow command",
    "allow this command",
    "approve command",
)
_AUTH_TRIGGERS = (
    "login",
    "sign in",
    "authentication",
    "api key",
)
_PROVIDER_ERROR_TRIGGERS = (
    "failed to start provider",
    "failed to start codex",
    "failed to start claude",
)
_INTERRUPT_SPEC = RuntimeInterruptSpec(
    trust_triggers=_TRUST_TRIGGERS,
    permission_triggers=_PERMISSION_TRIGGERS,
    login_triggers=_AUTH_TRIGGERS,
    welcome_triggers=_WELCOME_TRIGGERS,
    provider_error_triggers=_PROVIDER_ERROR_TRIGGERS,
)


class ClaudeCodeRuntime(ProviderRuntime):
    """Minimal first-pass Claude Code runtime on the new contract."""

    provider_name = "claude_code"

    def __init__(
        self,
        *,
        tmux_session: str = "atc",
        claude_command: str = "claude --dangerously-skip-permissions",
    ) -> None:
        self.tmux_session = tmux_session
        self.claude_command = claude_command

    async def prepare_workspace(self, request: StartRoleRequest) -> None:
        if request.working_dir:
            from os import makedirs

            makedirs(request.working_dir, exist_ok=True)

    async def start_role(self, request: StartRoleRequest) -> RuntimeSessionHandle:
        await ensure_tmux_session(self.tmux_session)
        command = build_path_env_prefix() + self.claude_command
        pane_id = await spawn_window_pane(
            self.tmux_session,
            command,
            working_dir=request.working_dir,
        )
        return RuntimeSessionHandle(
            session_id=request.session_id,
            provider_name=self.provider_name,
            role=request.role,
            transport=RuntimeTransport.TMUX,
            project_id=request.project_id,
            tmux_session=self.tmux_session,
            tmux_pane=pane_id,
            working_dir=request.working_dir,
            context_ref=request.context_ref,
            metadata={"display_name": request.display_name} if request.display_name else {},
        )

    async def spawn_existing_session(self, request: StartRoleRequest) -> RuntimeSessionHandle:
        return await self.start_role(request)

    async def stop_role(
        self,
        handle: RuntimeSessionHandle,
        request: StopRoleRequest | None = None,
    ) -> None:
        if handle.tmux_pane:
            await kill_pane(handle.tmux_pane)

    async def send_instruction(
        self,
        handle: RuntimeSessionHandle,
        request: InstructionRequest,
    ) -> None:
        # Phase 2 routes Codex through TmuxSessionRunner first. Claude keeps this
        # compatibility implementation until its provider-specific verification
        # semantics are migrated in a later phase.
        trace_id = str(request.metadata.get("delivery_trace_id") or "")
        if not handle.tmux_pane:
            self._append_instruction_trace(
                request,
                handle,
                trace_id,
                DeliveryStage.FAILED,
                DeliveryVerdict.FAILED,
                DeliveryReasonCode.PANE_MISSING,
            )
            raise RuntimeSessionMissingError("Claude session has no tmux pane recorded")
        if not await pane_exists(handle.tmux_pane):
            self._append_instruction_trace(
                request,
                handle,
                trace_id,
                DeliveryStage.FAILED,
                DeliveryVerdict.FAILED,
                DeliveryReasonCode.PANE_MISSING,
            )
            raise RuntimeSessionMissingError("Claude session pane is missing")

        text = request.message or ""
        if not text and request.message_file:
            with open(request.message_file, encoding="utf-8") as f:
                text = f.read()
        if not text:
            self._append_instruction_trace(
                request,
                handle,
                trace_id,
                DeliveryStage.FAILED,
                DeliveryVerdict.FAILED,
                DeliveryReasonCode.EMPTY_PAYLOAD,
            )
            raise RuntimeDeliveryError("Instruction payload was empty")

        before = await capture_pane_text(handle.tmux_pane, lines=40)
        before_state = self._prompt_state_for_excerpt(before)
        before_block_reason = self._block_reason_for_prompt_state(before_state)
        if before_block_reason is not None:
            self._append_instruction_trace(
                request,
                handle,
                trace_id,
                DeliveryStage.BLOCKED,
                DeliveryVerdict.BLOCKED,
                before_block_reason,
                prompt_state_before=before_state,
                prompt_state_after=before_state,
                first_output_excerpt=before,
            )
            return
        self._append_instruction_trace(
            request,
            handle,
            trace_id,
            DeliveryStage.WRITE_STARTED,
            DeliveryVerdict.PENDING,
            DeliveryReasonCode.PTY_WRITE_STARTED,
            prompt_state_before=before_state,
            first_output_excerpt=before,
        )
        await send_bracketed_instruction(self.tmux_session, handle.tmux_pane, text)
        self._append_instruction_trace(
            request,
            handle,
            trace_id,
            DeliveryStage.SUBMIT_ATTEMPTED,
            DeliveryVerdict.ACCEPTED,
            DeliveryReasonCode.SUBMIT_SENT,
            prompt_state_before=before_state,
        )
        await asyncio.sleep(0.75)
        output = await capture_pane_text(handle.tmux_pane, lines=80)
        after_state = self._prompt_state_for_excerpt(output)
        lowered = output.lower()
        fingerprint = text[:80].strip()

        self._append_instruction_trace(
            request,
            handle,
            trace_id,
            DeliveryStage.WRITTEN_TO_PTY,
            DeliveryVerdict.ACCEPTED,
            DeliveryReasonCode.PTY_WRITE_ACCEPTED,
            prompt_state_before=before_state,
            prompt_state_after=after_state,
            first_output_excerpt=output,
        )
        if after_state == "blocked:trust":
            self._append_instruction_trace(
                request,
                handle,
                trace_id,
                DeliveryStage.BLOCKED,
                DeliveryVerdict.BLOCKED,
                DeliveryReasonCode.TRUST_REQUIRED,
                prompt_state_before=before_state,
                prompt_state_after=after_state,
                first_output_excerpt=output,
            )
            return
        if after_state == "blocked:auth":
            self._append_instruction_trace(
                request,
                handle,
                trace_id,
                DeliveryStage.BLOCKED,
                DeliveryVerdict.BLOCKED,
                DeliveryReasonCode.AUTH_REQUIRED,
                prompt_state_before=before_state,
                prompt_state_after=after_state,
                first_output_excerpt=output,
            )
            return
        if after_state == "blocked:permission":
            self._append_instruction_trace(
                request,
                handle,
                trace_id,
                DeliveryStage.BLOCKED,
                DeliveryVerdict.BLOCKED,
                DeliveryReasonCode.PERMISSION_REQUIRED,
                prompt_state_before=before_state,
                prompt_state_after=after_state,
                first_output_excerpt=output,
            )
            return
        if fingerprint and fingerprint in output:
            self._append_instruction_trace(
                request,
                handle,
                trace_id,
                DeliveryStage.AGENT_OUTPUT_OBSERVED,
                DeliveryVerdict.CONFIRMED,
                DeliveryReasonCode.AGENT_OUTPUT,
                prompt_state_before=before_state,
                prompt_state_after=after_state,
                first_output_excerpt=output,
            )
            return
        if any(trigger in lowered for trigger in _WELCOME_TRIGGERS):
            self._append_instruction_trace(
                request,
                handle,
                trace_id,
                DeliveryStage.AGENT_OUTPUT_OBSERVED,
                DeliveryVerdict.ACCEPTED,
                DeliveryReasonCode.AGENT_OUTPUT,
                prompt_state_before=before_state,
                prompt_state_after=after_state,
                first_output_excerpt=output,
            )
            return
        if not _BARE_PROMPT_RE.search(output):
            self._append_instruction_trace(
                request,
                handle,
                trace_id,
                DeliveryStage.CONFIRMED_RUNNING,
                DeliveryVerdict.CONFIRMED,
                DeliveryReasonCode.SESSION_RUNNING,
                prompt_state_before=before_state,
                prompt_state_after=after_state,
                first_output_excerpt=output,
            )
            return

        self._append_instruction_trace(
            request,
            handle,
            trace_id,
            DeliveryStage.FAILED,
            DeliveryVerdict.FAILED,
            DeliveryReasonCode.DELIVERY_UNVERIFIED,
            prompt_state_before=before_state,
            prompt_state_after=after_state,
            first_output_excerpt=output,
        )
        raise RuntimeDeliveryError("Claude instruction delivery could not be verified")

    async def assign_task(
        self,
        handle: RuntimeSessionHandle,
        request: TaskAssignmentRequest,
    ) -> None:
        text = request.message
        if not text and request.message_file:
            with open(request.message_file, encoding="utf-8") as f:
                text = f.read()
        await self.send_instruction(
            handle,
            InstructionRequest(
                session_id=request.session_id,
                message=text,
                context_ref=request.context_ref,
                instruction_id=request.assignment_id or request.task_id,
                metadata=request.metadata,
            ),
        )

    async def check_readiness(
        self,
        handle: RuntimeSessionHandle,
    ) -> ReadinessResult:
        inspection = await self.inspect_session(handle)
        return ReadinessResult(
            session_id=inspection.session_id,
            provider_name=inspection.provider_name,
            state=inspection.readiness,
            block_reason=inspection.block_reason,
            summary=inspection.summary,
            details=inspection.details,
        )

    async def inspect_session(
        self,
        handle: RuntimeSessionHandle,
    ) -> RuntimeInspection:
        if not handle.tmux_pane or not await pane_exists(handle.tmux_pane):
            return RuntimeInspection(
                session_id=handle.session_id,
                provider_name=self.provider_name,
                alive=False,
                readiness=ReadinessState.STOPPED,
                summary="Pane missing",
            )

        excerpt = await capture_pane_text(handle.tmux_pane, lines=40)
        interrupt = self._detect_interrupt(excerpt)
        readiness, block_reason = self._classify_readiness(excerpt)
        summary = (
            interrupt.summary if interrupt else self._summary_for_state(readiness, block_reason)
        )
        inspection = RuntimeInspection(
            session_id=handle.session_id,
            provider_name=self.provider_name,
            alive=True,
            readiness=readiness,
            block_reason=block_reason,
            summary=summary,
            last_output_excerpt=excerpt,
        )
        if interrupt is not None:
            inspection.details.update(interrupt.to_trace_details())
        inspection.details["provider_runtime_hint"] = self._runtime_hint_for_inspection(inspection)
        inspection.details["provider_runtime_action"] = self._runtime_action_for_inspection(
            inspection
        )
        return inspection

    async def submit_pending_prompt(
        self,
        handle: RuntimeSessionHandle,
        inspection: RuntimeInspection,
    ) -> bool:
        """Submit a visible Claude prompt by sending Enter only.

        Claude does not yet expose provider-owned pending-payload text extraction,
        so callers should only reach this after neutral safety checks prove the
        pending prompt matches the persisted payload.
        """

        if not handle.tmux_pane:
            return False
        if inspection.readiness is not ReadinessState.READY:
            return False
        if inspection.block_reason is not None:
            return False
        await run_tmux("send-keys", "-t", handle.tmux_pane, "Enter")
        return True

    def _runtime_hint_for_inspection(self, inspection: RuntimeInspection) -> str:
        if inspection.readiness is ReadinessState.READY:
            return "prompt_visible"
        if inspection.readiness is ReadinessState.BUSY and inspection.last_output_excerpt:
            lowered = inspection.last_output_excerpt.lower()
            if any(trigger in lowered for trigger in _WELCOME_TRIGGERS):
                return "startup_banner"
            return "processing"
        if (
            inspection.readiness is ReadinessState.BLOCKED
            and inspection.block_reason is RuntimeBlockReason.TRUST
        ):
            return "trust_prompt"
        if (
            inspection.readiness is ReadinessState.BLOCKED
            and inspection.block_reason is RuntimeBlockReason.AUTH
        ):
            return "auth_prompt"
        if (
            inspection.readiness is ReadinessState.BLOCKED
            and inspection.block_reason is RuntimeBlockReason.PERMISSION
        ):
            return "permission_prompt"
        if inspection.readiness is ReadinessState.STOPPED:
            return "pane_missing"
        return inspection.readiness.value

    def _runtime_action_for_inspection(self, inspection: RuntimeInspection) -> str:
        if inspection.readiness is ReadinessState.READY:
            return "send_or_resume"
        if inspection.readiness is ReadinessState.BUSY:
            return "wait"
        if (
            inspection.readiness is ReadinessState.BLOCKED
            and inspection.block_reason is RuntimeBlockReason.TRUST
        ):
            return "resolve_trust"
        if (
            inspection.readiness is ReadinessState.BLOCKED
            and inspection.block_reason is RuntimeBlockReason.AUTH
        ):
            return "resolve_auth"
        if (
            inspection.readiness is ReadinessState.BLOCKED
            and inspection.block_reason is RuntimeBlockReason.PERMISSION
        ):
            return "resolve_permission"
        if inspection.readiness is ReadinessState.STOPPED:
            return "respawn"
        return "inspect"

    async def restore_session(
        self,
        handle: RuntimeSessionHandle,
    ) -> RuntimeInspection:
        inspection = await self.inspect_session(handle)
        details = dict(inspection.details)
        details["restore_attempted"] = True
        details["restore_usable"] = inspection.readiness in {
            ReadinessState.READY,
            ReadinessState.BUSY,
        }
        details["restore_needs_attention"] = inspection.readiness in {
            ReadinessState.BLOCKED,
            ReadinessState.STOPPED,
            ReadinessState.ERROR,
        }
        details["provider_restore_stage"] = self._restore_stage_for_inspection(inspection)
        details["provider_restore_action"] = self._restore_action_for_inspection(inspection)
        summary = inspection.summary
        if inspection.readiness is ReadinessState.READY:
            summary = "Restored and ready"
        elif inspection.readiness is ReadinessState.BUSY:
            summary = "Restored but still active"
        elif inspection.readiness is ReadinessState.BLOCKED:
            summary = f"Restore blocked: {inspection.summary}"
        elif inspection.readiness is ReadinessState.STOPPED:
            summary = "Restore failed: pane missing"
        return RuntimeInspection(
            session_id=inspection.session_id,
            provider_name=inspection.provider_name,
            alive=inspection.alive,
            readiness=inspection.readiness,
            block_reason=inspection.block_reason,
            summary=summary,
            last_output_excerpt=inspection.last_output_excerpt,
            details=details,
        )

    def _restore_stage_for_inspection(self, inspection: RuntimeInspection) -> str:
        if inspection.readiness is ReadinessState.READY:
            return "ready"
        if inspection.readiness is ReadinessState.BUSY and inspection.last_output_excerpt:
            lowered = inspection.last_output_excerpt.lower()
            if any(trigger in lowered for trigger in _WELCOME_TRIGGERS):
                return "warming_up"
            return "active"
        if (
            inspection.readiness is ReadinessState.BLOCKED
            and inspection.block_reason is RuntimeBlockReason.TRUST
        ):
            return "trust_gate"
        if (
            inspection.readiness is ReadinessState.BLOCKED
            and inspection.block_reason is RuntimeBlockReason.AUTH
        ):
            return "auth_gate"
        if (
            inspection.readiness is ReadinessState.BLOCKED
            and inspection.block_reason is RuntimeBlockReason.PERMISSION
        ):
            return "permission_gate"
        if inspection.readiness is ReadinessState.STOPPED:
            return "missing"
        return inspection.readiness.value

    def _restore_action_for_inspection(self, inspection: RuntimeInspection) -> str:
        if inspection.readiness is ReadinessState.READY:
            return "resume"
        if inspection.readiness is ReadinessState.BUSY:
            return "wait"
        if (
            inspection.readiness is ReadinessState.BLOCKED
            and inspection.block_reason is RuntimeBlockReason.TRUST
        ):
            return "resolve_trust"
        if (
            inspection.readiness is ReadinessState.BLOCKED
            and inspection.block_reason is RuntimeBlockReason.AUTH
        ):
            return "resolve_auth"
        if (
            inspection.readiness is ReadinessState.BLOCKED
            and inspection.block_reason is RuntimeBlockReason.PERMISSION
        ):
            return "resolve_permission"
        if inspection.readiness is ReadinessState.STOPPED:
            return "respawn"
        return "inspect"

    def _classify_readiness(self, excerpt: str) -> tuple[ReadinessState, RuntimeBlockReason | None]:
        if _BARE_PROMPT_RE.search(excerpt):
            return ReadinessState.READY, None
        interrupt = self._detect_interrupt(excerpt)
        if interrupt is not None:
            return interrupt.readiness, interrupt.block_reason
        return ReadinessState.BUSY, None

    def _prompt_state_for_excerpt(self, excerpt: str) -> str:
        if _BARE_PROMPT_RE.search(excerpt):
            return ReadinessState.READY.value
        readiness, block_reason = self._classify_readiness(excerpt)
        interrupt = self._detect_interrupt(excerpt)
        fallback = f"{readiness.value}:{block_reason.value}" if block_reason else readiness.value
        return interrupt_prompt_state(interrupt, fallback)

    @staticmethod
    def _detect_interrupt(excerpt: str):
        return detect_runtime_interrupt(excerpt, _INTERRUPT_SPEC)

    @staticmethod
    def _block_reason_for_prompt_state(prompt_state: str) -> DeliveryReasonCode | None:
        if prompt_state == "blocked:trust":
            return DeliveryReasonCode.TRUST_REQUIRED
        if prompt_state == "blocked:auth":
            return DeliveryReasonCode.AUTH_REQUIRED
        if prompt_state == "blocked:permission":
            return DeliveryReasonCode.PERMISSION_REQUIRED
        return None

    @staticmethod
    def _append_instruction_trace(
        request: InstructionRequest,
        handle: RuntimeSessionHandle,
        trace_id: str,
        stage: DeliveryStage,
        verdict: DeliveryVerdict,
        reason_code: DeliveryReasonCode,
        *,
        prompt_state_before: str | None = None,
        prompt_state_after: str | None = None,
        first_output_excerpt: str | None = None,
    ) -> None:
        if not trace_id:
            return
        raw_action = request.metadata.get("delivery_action")
        action = (
            DeliveryAction.TASK_ASSIGNMENT
            if raw_action == DeliveryAction.TASK_ASSIGNMENT.value
            else DeliveryAction.INSTRUCTION
        )
        append_trace_event(
            request.metadata,
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

    @staticmethod
    def _summary_for_state(
        readiness: ReadinessState,
        block_reason: RuntimeBlockReason | None,
    ) -> str:
        if readiness is ReadinessState.READY:
            return "Prompt ready"
        if readiness is ReadinessState.BLOCKED and block_reason is RuntimeBlockReason.TRUST:
            return "Blocked on trust prompt"
        if readiness is ReadinessState.BLOCKED and block_reason is RuntimeBlockReason.AUTH:
            return "Blocked on authentication"
        if readiness is ReadinessState.BLOCKED and block_reason is RuntimeBlockReason.PERMISSION:
            return "Blocked on permission prompt"
        if readiness is ReadinessState.BUSY:
            return "Session active but not at bare prompt"
        if readiness is ReadinessState.STOPPED:
            return "Pane missing"
        return readiness.value

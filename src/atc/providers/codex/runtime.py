"""First Codex provider runtime implementation on the new contract."""

from __future__ import annotations

from atc.providers.base import ProviderRuntime
from atc.providers.codex.classifier import CODEX_PROMPT_RE, CodexRuntimeClassifier
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
from atc.runtime.tmux.runner import RunnerTerminalVerdict, TmuxSessionRunner
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
)


class CodexRuntime(ProviderRuntime):
    """Minimal first-pass Codex runtime on the new contract."""

    provider_name = "codex"

    def __init__(
        self,
        *,
        tmux_session: str = "atc",
        codex_command: str = "codex",
    ) -> None:
        self.tmux_session = tmux_session
        self.codex_command = codex_command
        self.classifier = CodexRuntimeClassifier()

    def _prompt_state_for_excerpt(self, excerpt: str) -> str:
        """Compatibility wrapper for older scenario tests."""

        return self.classifier.prompt_state_for_excerpt(excerpt)

    def _classify_readiness(
        self, excerpt: str
    ) -> tuple[ReadinessState, RuntimeBlockReason | None]:
        """Compatibility wrapper around the provider-neutral classifier."""

        classification = self.classifier.classify_excerpt(excerpt)
        return classification.readiness, classification.block_reason

    async def prepare_workspace(self, request: StartRoleRequest) -> None:
        if request.working_dir:
            from os import makedirs

            makedirs(request.working_dir, exist_ok=True)

    async def start_role(self, request: StartRoleRequest) -> RuntimeSessionHandle:
        await ensure_tmux_session(self.tmux_session)
        command = build_path_env_prefix() + self.codex_command
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
        trace_id = str(request.metadata.get("delivery_trace_id") or "")
        action = self._delivery_action_from_metadata(request.metadata)
        runner = TmuxSessionRunner(
            tmux_session=self.tmux_session,
            provider_name=self.provider_name,
            prompt_state_for_excerpt=self.classifier.prompt_state_for_excerpt,
            terminal_verdict_for_observation=self._terminal_verdict_for_observation,
            interrupt_detector=self.classifier.blocking_interrupt_for_excerpt,
        )
        await runner.deliver_instruction(
            handle=handle,
            metadata=request.metadata,
            trace_id=trace_id,
            action=action,
            payload_loader=lambda: self._instruction_text(request),
        )

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
            classification = self.classifier.classify_excerpt("", pane_missing=True)
            return RuntimeInspection(
                session_id=handle.session_id,
                provider_name=self.provider_name,
                alive=False,
                readiness=classification.readiness,
                block_reason=classification.block_reason,
                summary=classification.summary,
                details=classification.as_details(),
            )

        excerpt = await capture_pane_text(handle.tmux_pane, lines=40)
        classification = self.classifier.classify_excerpt(excerpt)
        inspection = RuntimeInspection(
            session_id=handle.session_id,
            provider_name=self.provider_name,
            alive=True,
            readiness=classification.readiness,
            block_reason=classification.block_reason,
            summary=classification.summary,
            last_output_excerpt=excerpt,
            details=classification.as_details(),
        )
        inspection.details["recovery_capabilities"] = (
            self.classifier.recovery_capabilities().as_dict()
        )
        inspection.details["provider_runtime_hint"] = self._runtime_hint_for_inspection(inspection)
        inspection.details["provider_runtime_action"] = self._runtime_action_for_inspection(
            inspection
        )
        return inspection

    async def resolve_startup_prompt(
        self,
        handle: RuntimeSessionHandle,
        inspection: RuntimeInspection,
    ) -> bool:
        """Resolve only Codex startup prompts declared safe by provider classification."""

        if not handle.tmux_pane:
            return False
        if inspection.block_reason is not RuntimeBlockReason.TRUST:
            return False
        diagnostics = inspection.details.get("provider_diagnostics")
        if not isinstance(diagnostics, dict):
            return False
        if diagnostics.get("safe_to_auto_resolve") is not True:
            return False
        capabilities = inspection.details.get("recovery_capabilities")
        if not isinstance(capabilities, dict) or not capabilities.get(
            "can_auto_accept_managed_workspace_trust_prompt"
        ):
            return False
        await run_tmux("send-keys", "-t", handle.tmux_pane, "Enter")
        return True

    async def submit_pending_prompt(
        self,
        handle: RuntimeSessionHandle,
        inspection: RuntimeInspection,
    ) -> bool:
        """Submit a provider-classified visible Codex prompt by sending Enter only."""

        if not handle.tmux_pane:
            return False
        if inspection.details.get("blocker_reason") != "prompt_not_submitted":
            return False
        diagnostics = inspection.details.get("provider_diagnostics")
        if not isinstance(diagnostics, dict) or not diagnostics.get("pending_prompt_text"):
            return False
        await run_tmux("send-keys", "-t", handle.tmux_pane, "Enter")
        return True

    def _runtime_hint_for_inspection(self, inspection: RuntimeInspection) -> str:
        if inspection.readiness is ReadinessState.READY:
            return "prompt_visible"
        if inspection.readiness is ReadinessState.BUSY:
            return "processing"
        if inspection.readiness is ReadinessState.BLOCKED:
            if inspection.block_reason is RuntimeBlockReason.AUTH:
                return "auth_prompt"
            if inspection.block_reason is RuntimeBlockReason.TRUST:
                return "trust_prompt"
            if inspection.block_reason is RuntimeBlockReason.PERMISSION:
                return "permission_prompt"
            return "blocked_prompt"
        if inspection.readiness is ReadinessState.STOPPED:
            return "pane_missing"
        return inspection.readiness.value

    def _runtime_action_for_inspection(self, inspection: RuntimeInspection) -> str:
        if inspection.readiness is ReadinessState.READY:
            return "send_or_resume"
        if inspection.readiness is ReadinessState.BUSY:
            return "wait"
        if inspection.readiness is ReadinessState.BLOCKED:
            if inspection.block_reason is RuntimeBlockReason.AUTH:
                return "resolve_auth"
            if inspection.block_reason is RuntimeBlockReason.TRUST:
                return "resolve_trust"
            if inspection.block_reason is RuntimeBlockReason.PERMISSION:
                return "resolve_permission"
            return "inspect_prompt"
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
        if inspection.readiness is ReadinessState.BUSY:
            return "active"
        if inspection.readiness is ReadinessState.BLOCKED:
            if inspection.block_reason is RuntimeBlockReason.AUTH:
                return "auth_gate"
            if inspection.block_reason is RuntimeBlockReason.TRUST:
                return "trust_gate"
            if inspection.block_reason is RuntimeBlockReason.PERMISSION:
                return "permission_gate"
            return "blocked_prompt"
        if inspection.readiness is ReadinessState.STOPPED:
            return "missing"
        return inspection.readiness.value

    def _restore_action_for_inspection(self, inspection: RuntimeInspection) -> str:
        if inspection.readiness is ReadinessState.READY:
            return "resume"
        if inspection.readiness is ReadinessState.BUSY:
            return "wait"
        if inspection.readiness is ReadinessState.BLOCKED:
            if inspection.block_reason is RuntimeBlockReason.AUTH:
                return "resolve_auth"
            if inspection.block_reason is RuntimeBlockReason.TRUST:
                return "resolve_trust"
            if inspection.block_reason is RuntimeBlockReason.PERMISSION:
                return "resolve_permission"
            return "inspect_prompt"
        if inspection.readiness is ReadinessState.STOPPED:
            return "respawn"
        return "inspect"

    def _detect_interrupt(self, excerpt: str):
        return self.classifier.detect_interrupt(excerpt)

    @staticmethod
    def _delivery_action_from_metadata(metadata: dict[str, object]) -> DeliveryAction:
        raw_action = metadata.get("delivery_action")
        if raw_action == DeliveryAction.TASK_ASSIGNMENT.value:
            return DeliveryAction.TASK_ASSIGNMENT
        return DeliveryAction.INSTRUCTION

    @staticmethod
    async def _instruction_text(request: InstructionRequest) -> str:
        text = request.message or ""
        if not text and request.message_file:
            with open(request.message_file, encoding="utf-8") as f:
                text = f.read()
        return text

    @staticmethod
    def _terminal_verdict_for_observation(
        _text: str,
        after_state: str | None,
        output: str,
    ) -> RunnerTerminalVerdict:
        if after_state == "blocked:trust":
            return RunnerTerminalVerdict(
                stage=DeliveryStage.BLOCKED,
                verdict=DeliveryVerdict.BLOCKED,
                reason_code=DeliveryReasonCode.TRUST_REQUIRED,
            )
        if after_state == "blocked:auth":
            return RunnerTerminalVerdict(
                stage=DeliveryStage.BLOCKED,
                verdict=DeliveryVerdict.BLOCKED,
                reason_code=DeliveryReasonCode.AUTH_REQUIRED,
            )
        if after_state == "blocked:permission":
            return RunnerTerminalVerdict(
                stage=DeliveryStage.BLOCKED,
                verdict=DeliveryVerdict.BLOCKED,
                reason_code=DeliveryReasonCode.PERMISSION_REQUIRED,
            )
        if after_state == "blocked:runtime_update_required":
            return RunnerTerminalVerdict(
                stage=DeliveryStage.BLOCKED,
                verdict=DeliveryVerdict.BLOCKED,
                reason_code=DeliveryReasonCode.RUNTIME_UPDATE_REQUIRED,
            )
        if after_state == "prompt_visible:not_submitted":
            return RunnerTerminalVerdict(
                stage=DeliveryStage.BLOCKED,
                verdict=DeliveryVerdict.BLOCKED,
                reason_code=DeliveryReasonCode.PROMPT_NOT_SUBMITTED,
            )
        if CODEX_PROMPT_RE.search(output):
            return RunnerTerminalVerdict(
                stage=DeliveryStage.PROMPT_CLEARED,
                verdict=DeliveryVerdict.ACCEPTED,
                reason_code=DeliveryReasonCode.PROMPT_STILL_VISIBLE,
            )
        if output.strip():
            return RunnerTerminalVerdict(
                stage=DeliveryStage.AGENT_OUTPUT_OBSERVED,
                verdict=DeliveryVerdict.CONFIRMED,
                reason_code=DeliveryReasonCode.AGENT_OUTPUT,
            )
        return RunnerTerminalVerdict(
            stage=DeliveryStage.CONFIRMED_RUNNING,
            verdict=DeliveryVerdict.CONFIRMED,
            reason_code=DeliveryReasonCode.SESSION_RUNNING,
        )

    @staticmethod
    def _summary_for_state(
        readiness: ReadinessState,
        block_reason: RuntimeBlockReason | None,
    ) -> str:
        if readiness is ReadinessState.READY:
            return "Prompt ready"
        if readiness is ReadinessState.BLOCKED and block_reason is RuntimeBlockReason.AUTH:
            return "Blocked on authentication"
        if readiness is ReadinessState.BLOCKED and block_reason is RuntimeBlockReason.TRUST:
            return "Blocked on trust prompt"
        if readiness is ReadinessState.BLOCKED and block_reason is RuntimeBlockReason.PERMISSION:
            return "Blocked on permission prompt"
        if readiness is ReadinessState.ERROR:
            return "Provider error"
        if readiness is ReadinessState.BUSY:
            return "Session active but not at prompt"
        if readiness is ReadinessState.STOPPED:
            return "Pane missing"
        return readiness.value

"""First Codex provider runtime implementation on the new contract."""

from __future__ import annotations

import re

from atc.providers.base import ProviderRuntime
from atc.runtime.errors import RuntimeDeliveryError, RuntimeSessionMissingError
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
    spawn_window_pane,
)

_CODEX_PROMPT_RE = re.compile(r"(^|\n)\s*(❯|>)\s*$", re.MULTILINE)
_AUTH_TRIGGERS = (
    "login",
    "sign in",
    "authentication",
    "api key",
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
        if not handle.tmux_pane:
            raise RuntimeSessionMissingError("Codex session has no tmux pane recorded")
        if not await pane_exists(handle.tmux_pane):
            raise RuntimeSessionMissingError("Codex session pane is missing")
        text = request.message or ""
        if not text and request.message_file:
            text = open(request.message_file, encoding="utf-8").read()
        if not text:
            raise RuntimeDeliveryError("Instruction payload was empty")
        await send_bracketed_instruction(self.tmux_session, handle.tmux_pane, text)

    async def assign_task(
        self,
        handle: RuntimeSessionHandle,
        request: TaskAssignmentRequest,
    ) -> None:
        text = request.message
        if not text and request.message_file:
            text = open(request.message_file, encoding="utf-8").read()
        await self.send_instruction(
            handle,
            InstructionRequest(
                session_id=request.session_id,
                message=text,
                context_ref=request.context_ref,
                instruction_id=request.assignment_id or request.task_id,
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
        readiness, block_reason = self._classify_readiness(excerpt)
        inspection = RuntimeInspection(
            session_id=handle.session_id,
            provider_name=self.provider_name,
            alive=True,
            readiness=readiness,
            block_reason=block_reason,
            summary=self._summary_for_state(readiness, block_reason),
            last_output_excerpt=excerpt,
        )
        inspection.details["provider_runtime_hint"] = self._runtime_hint_for_inspection(inspection)
        inspection.details["provider_runtime_action"] = self._runtime_action_for_inspection(inspection)
        return inspection

    def _runtime_hint_for_inspection(self, inspection: RuntimeInspection) -> str:
        if inspection.readiness is ReadinessState.READY:
            return "prompt_visible"
        if inspection.readiness is ReadinessState.BUSY:
            return "processing"
        if inspection.readiness is ReadinessState.BLOCKED and inspection.block_reason is RuntimeBlockReason.AUTH:
            return "auth_prompt"
        if inspection.readiness is ReadinessState.STOPPED:
            return "pane_missing"
        return inspection.readiness.value

    def _runtime_action_for_inspection(self, inspection: RuntimeInspection) -> str:
        if inspection.readiness is ReadinessState.READY:
            return "send_or_resume"
        if inspection.readiness is ReadinessState.BUSY:
            return "wait"
        if inspection.readiness is ReadinessState.BLOCKED and inspection.block_reason is RuntimeBlockReason.AUTH:
            return "resolve_auth"
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
        if inspection.readiness is ReadinessState.BLOCKED and inspection.block_reason is RuntimeBlockReason.AUTH:
            return "auth_gate"
        if inspection.readiness is ReadinessState.STOPPED:
            return "missing"
        return inspection.readiness.value

    def _restore_action_for_inspection(self, inspection: RuntimeInspection) -> str:
        if inspection.readiness is ReadinessState.READY:
            return "resume"
        if inspection.readiness is ReadinessState.BUSY:
            return "wait"
        if inspection.readiness is ReadinessState.BLOCKED and inspection.block_reason is RuntimeBlockReason.AUTH:
            return "resolve_auth"
        if inspection.readiness is ReadinessState.STOPPED:
            return "respawn"
        return "inspect"

    def _classify_readiness(self, excerpt: str) -> tuple[ReadinessState, RuntimeBlockReason | None]:
        lowered = excerpt.lower()
        if any(trigger in lowered for trigger in _AUTH_TRIGGERS):
            return ReadinessState.BLOCKED, RuntimeBlockReason.AUTH
        if _CODEX_PROMPT_RE.search(excerpt):
            return ReadinessState.READY, None
        return ReadinessState.BUSY, None

    @staticmethod
    def _summary_for_state(
        readiness: ReadinessState,
        block_reason: RuntimeBlockReason | None,
    ) -> str:
        if readiness is ReadinessState.READY:
            return "Prompt ready"
        if readiness is ReadinessState.BLOCKED and block_reason is RuntimeBlockReason.AUTH:
            return "Blocked on authentication"
        if readiness is ReadinessState.BUSY:
            return "Session active but not at prompt"
        if readiness is ReadinessState.STOPPED:
            return "Pane missing"
        return readiness.value

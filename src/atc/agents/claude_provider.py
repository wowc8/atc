"""ClaudeCodeProvider — wraps the existing tmux + PTY approach behind AgentProvider.

Spawns Claude Code sessions in tmux panes, sends prompts via tmux send-keys,
and monitors output through PTY streaming. This is the original ATC approach
wrapped behind the provider abstraction.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from atc.agents.base import (
    OutputChunk,
    PromptResult,
    ProviderCapabilities,
    ProviderError,
    SessionInfo,
    SessionStatus,
)
from atc.agents.claude_runtime import (
    accept_startup_dialogs,
    send_instruction as claude_send_instruction,
    wait_for_prompt as claude_wait_for_prompt,
)
from atc.terminal.control import send_instruction_async

logger = logging.getLogger(__name__)

_TMUX_CMD = "tmux"


class ClaudeCodeProvider:
    """Agent provider using Claude Code in tmux panes.

    Sessions are spawned as tmux new-window commands. Prompts are delivered
    through Claude-specific runtime helpers. Output streaming delegates to a
    capture-pane fallback for now.

    Implements :class:`AgentProvider` protocol.
    """

    def __init__(
        self,
        *,
        tmux_session: str = "atc",
        claude_command: str = "claude",
    ) -> None:
        self._tmux_session = tmux_session
        self._claude_command = claude_command
        self._sessions: dict[str, _TrackedSession] = {}

    @property
    def name(self) -> str:
        return "claude_code"

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_streaming=True,
            supports_tool_use=True,
            context_window=200_000,
            model="claude",
        )

    async def spawn_session(
        self,
        session_id: str,
        *,
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
        context_file: Path | None = None,
        role: str = "ace",
    ) -> SessionInfo:
        if session_id in self._sessions:
            raise ProviderError(self.name, f"Session {session_id} already exists")

        self._check_tmux_available()

        if working_dir:
            await self.prepare_workspace(
                session_id, working_dir=working_dir, context_file=context_file
            )

        import shlex as _shlex

        from atc.agents.auth import resolve_agent_api_key

        cmd_parts = [self._claude_command]
        all_env: dict[str, str] = {}

        api_key = resolve_agent_api_key()
        if api_key:
            all_env["ANTHROPIC_API_KEY"] = api_key

        if env:
            all_env.update(env)

        env_prefix = ""
        if all_env:
            env_parts = [f"{k}={_shlex.quote(v)}" for k, v in all_env.items()]
            env_prefix = " ".join(env_parts) + " "

        shell_cmd = f"{env_prefix}{' '.join(cmd_parts)}"
        logger.debug("spawn_session role=%s session=%s", role, session_id)

        tmux_args = [
            _TMUX_CMD,
            "new-window",
            "-t",
            self._tmux_session,
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
        ]
        if working_dir:
            tmux_args.extend(["-c", working_dir])
        tmux_args.append(shell_cmd)

        try:
            proc = await asyncio.create_subprocess_exec(
                *tmux_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except OSError as exc:
            raise ProviderError(self.name, f"Failed to spawn tmux pane: {exc}") from exc

        if proc.returncode != 0:
            err = stderr.decode().strip()
            raise ProviderError(self.name, f"tmux new-window failed: {err}")

        pane_id = stdout.decode().strip()
        tracked = _TrackedSession(
            session_id=session_id,
            pane_id=pane_id,
            status=SessionStatus.IDLE,
        )
        self._sessions[session_id] = tracked

        logger.info("Spawned Claude Code session %s in pane %s", session_id, pane_id)
        return tracked.to_info()

    async def handle_startup(self, session_id: str) -> None:
        """Handle Claude-specific startup dialogs for a tracked session."""
        tracked = self._get_tracked(session_id)
        await accept_startup_dialogs(
            tracked.pane_id,
            capture_pane=self._capture_pane,
            get_alternate_on=self._get_alternate_on,
            tmux_run=self._tmux_run,
        )

    async def send_prompt(self, session_id: str, prompt: str) -> PromptResult:
        tracked = self._get_tracked(session_id)

        import atc.agents.claude_runtime as _claude_runtime

        original_send = _claude_runtime.send_instruction_async
        _claude_runtime.send_instruction_async = send_instruction_async
        try:
            accepted = await claude_send_instruction(
                tracked.pane_id,
                prompt,
                capture_pane=self._capture_pane,
                pane_is_alive=self._pane_is_alive,
                wait_for_prompt_fn=self._wait_for_prompt,
                check_tui_ready_fn=self.is_ready,
            )
        except Exception as exc:
            raise ProviderError(self.name, f"send-keys failed: {exc}") from exc
        finally:
            _claude_runtime.send_instruction_async = original_send

        tracked.status = SessionStatus.BUSY if accepted else SessionStatus.ERROR
        logger.info("Sent prompt to session %s (pane %s)", session_id, tracked.pane_id)
        return PromptResult(session_id=session_id, accepted=accepted)

    async def get_status(self, session_id: str) -> SessionInfo:
        tracked = self._get_tracked(session_id)

        try:
            await self._tmux_query("has-session", "-t", tracked.pane_id)
        except ProviderError:
            tracked.status = SessionStatus.STOPPED
        return tracked.to_info()

    async def stream_output(self, session_id: str) -> AsyncIterator[OutputChunk]:
        tracked = self._get_tracked(session_id)
        content = await self._capture_pane(tracked.pane_id, lines=200)
        yield OutputChunk(
            session_id=session_id,
            content=content,
            is_final=True,
        )

    async def stop_session(self, session_id: str) -> None:
        tracked = self._get_tracked(session_id)

        try:
            proc = await asyncio.create_subprocess_exec(
                _TMUX_CMD,
                "kill-pane",
                "-t",
                tracked.pane_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
        except OSError as exc:
            raise ProviderError(self.name, f"kill-pane failed: {exc}") from exc

        if proc.returncode != 0:
            err = stderr.decode().strip()
            logger.warning("kill-pane for %s returned error: %s", session_id, err)

        tracked.status = SessionStatus.STOPPED
        del self._sessions[session_id]
        logger.info("Stopped session %s (pane %s)", session_id, tracked.pane_id)

    async def list_sessions(self) -> list[SessionInfo]:
        return [t.to_info() for t in self._sessions.values()]

    async def prepare_workspace(
        self,
        session_id: str,
        *,
        working_dir: str,
        context_file: Path | None = None,
    ) -> None:
        """Create working_dir and optionally copy context_file to CLAUDE.md.

        Skips the copy if working_dir/CLAUDE.md already exists so we never
        overwrite a file deployed by AceDeploySpec / ManagerDeploySpec.
        """
        import os as _os

        _os.makedirs(working_dir, exist_ok=True)
        logger.debug(
            "prepare_workspace: ensured %s exists (session %s)", working_dir, session_id
        )

        if context_file is not None:
            dest = Path(working_dir) / "CLAUDE.md"
            if not dest.exists():
                shutil.copy2(str(context_file), str(dest))
                logger.info(
                    "prepare_workspace: copied %s → %s (session %s)",
                    context_file,
                    dest,
                    session_id,
                )
            else:
                logger.debug(
                    "prepare_workspace: %s already exists, skipping copy (session %s)",
                    dest,
                    session_id,
                )

    async def is_ready(self, session_id: str) -> bool:
        """Return True when the session's tmux pane shows an idle Claude prompt."""
        tracked = self._sessions.get(session_id)
        if tracked is None:
            return False
        return await self._wait_for_prompt(tracked.pane_id)

    async def _tmux_query(self, *args: str) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                _TMUX_CMD,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except OSError as exc:
            raise ProviderError(self.name, f"tmux command failed: {exc}") from exc
        if proc.returncode != 0:
            raise ProviderError(self.name, stderr.decode().strip() or "tmux query failed")
        return stdout.decode()

    async def _capture_pane(self, pane_id: str, *, lines: int = 50) -> str:
        return await self._tmux_query("capture-pane", "-t", pane_id, "-p", "-S", f"-{lines}")

    async def _get_alternate_on(self, pane_id: str) -> bool:
        result = await self._tmux_query("display-message", "-t", pane_id, "-p", "#{alternate_on}")
        return result.strip() == "1"

    async def _pane_is_alive(self, pane_id: str) -> bool:
        try:
            await self._tmux_query("has-session", "-t", pane_id)
            return True
        except ProviderError:
            return False

    async def _wait_for_prompt(
        self,
        pane_id: str,
        *,
        timeout: float = 10.0,
        poll_interval: float = 0.5,
    ) -> bool:
        return await claude_wait_for_prompt(
            pane_id,
            get_alternate_on=self._get_alternate_on,
            capture_pane=self._capture_pane,
            timeout=timeout,
            poll_interval=poll_interval,
        )

    async def _tmux_run(self, *args: str) -> str:
        return (await self._tmux_query(*args)).strip()

    def _get_tracked(self, session_id: str) -> _TrackedSession:
        tracked = self._sessions.get(session_id)
        if tracked is None:
            raise ProviderError(self.name, f"Session {session_id} not found")
        return tracked

    def _check_tmux_available(self) -> None:
        if shutil.which(_TMUX_CMD) is None:
            raise ProviderError(self.name, "tmux is not installed or not on PATH")


class _TrackedSession:
    """Internal bookkeeping for a Claude Code session."""

    __slots__ = ("session_id", "pane_id", "status")

    def __init__(
        self,
        session_id: str,
        pane_id: str,
        status: SessionStatus,
    ) -> None:
        self.session_id = session_id
        self.pane_id = pane_id
        self.status = status

    def to_info(self) -> SessionInfo:
        return SessionInfo(
            session_id=self.session_id,
            status=self.status,
            metadata={"pane_id": self.pane_id},
        )

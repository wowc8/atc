"""CodexProvider — tmux-backed provider for OpenAI Codex CLI sessions."""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from atc.agents.base import (
    OutputChunk,
    PromptResult,
    ProviderCapabilities,
    ProviderError,
    ProviderSpawnRequest,
    SessionInfo,
    SessionStatus,
)
from atc.terminal.control import send_instruction_async
from atc.session.ace import _ensure_tmux_session

logger = logging.getLogger(__name__)

_TMUX_CMD = "tmux"


class CodexProvider:
    """Agent provider using Codex CLI in tmux panes.

    This starts Codex in a tmux window for the same terminal visibility model
    ATC already uses for Claude Code sessions.
    """

    def __init__(
        self,
        *,
        tmux_session: str = "atc",
        codex_command: str = "codex",
    ) -> None:
        self._tmux_session = tmux_session
        self._codex_command = codex_command
        self._sessions: dict[str, _TrackedSession] = {}

    @property
    def name(self) -> str:
        return "codex"

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_streaming=True,
            supports_tool_use=True,
            context_window=200_000,
            model="codex",
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
            await self.prepare_workspace(session_id, working_dir=working_dir, context_file=context_file)

        await _ensure_tmux_session(self._tmux_session)

        tmux_args = [_TMUX_CMD, "new-window", "-t", self._tmux_session, "-d", "-P", "-F", "#{pane_id}"]
        if working_dir:
            tmux_args.extend(["-c", working_dir])
        tmux_args.append(self._codex_command)

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
        tracked = _TrackedSession(session_id=session_id, pane_id=pane_id, status=SessionStatus.IDLE)
        self._sessions[session_id] = tracked
        logger.info("Spawned Codex session %s in pane %s", session_id, pane_id)
        return tracked.to_info()

    async def prepare_workspace(
        self,
        session_id: str,
        *,
        working_dir: str,
        context_file: Path | None = None,
    ) -> None:
        import os as _os

        _os.makedirs(working_dir, exist_ok=True)
        if context_file is not None:
            dest = Path(working_dir) / "CLAUDE.md"
            if not dest.exists():
                shutil.copy2(str(context_file), str(dest))
                logger.info("prepare_workspace: copied %s → %s (session %s)", context_file, dest, session_id)

    async def is_ready(self, session_id: str) -> bool:
        tracked = self._sessions.get(session_id)
        if tracked is None:
            return False
        return await self._pane_is_alive(tracked.pane_id)

    async def handle_startup(self, session_id: str) -> None:
        self._get_tracked(session_id)
        return None

    async def spawn_for_session(self, request: ProviderSpawnRequest) -> SessionInfo:
        if request.launch_command:
            self._codex_command = request.launch_command
        info = await self.spawn_session(
            request.session.id,
            working_dir=request.working_dir,
            env=request.env,
            context_file=request.context_file,
            role=request.role,
        )
        await self.handle_startup(request.session.id)
        return info

    async def send_prompt(self, session_id: str, prompt: str) -> PromptResult:
        tracked = self._get_tracked(session_id)
        try:
            await send_instruction_async(tracked.pane_id, prompt)
        except Exception as exc:
            raise ProviderError(self.name, f"send-keys failed: {exc}") from exc
        tracked.status = SessionStatus.BUSY
        return PromptResult(session_id=session_id, accepted=True)

    async def get_status(self, session_id: str) -> SessionInfo:
        tracked = self._get_tracked(session_id)
        alive = await self._pane_is_alive(tracked.pane_id)
        tracked.status = SessionStatus.IDLE if alive else SessionStatus.STOPPED
        return tracked.to_info()

    async def stream_output(self, session_id: str) -> AsyncIterator[OutputChunk]:
        tracked = self._get_tracked(session_id)
        content = await self._capture_pane(tracked.pane_id, lines=200)
        yield OutputChunk(session_id=session_id, content=content, is_final=True)

    async def stop_session(self, session_id: str) -> None:
        tracked = self._get_tracked(session_id)
        try:
            proc = await asyncio.create_subprocess_exec(
                _TMUX_CMD, "kill-pane", "-t", tracked.pane_id,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except OSError as exc:
            raise ProviderError(self.name, f"kill-pane failed: {exc}") from exc
        tracked.status = SessionStatus.STOPPED
        del self._sessions[session_id]

    async def list_sessions(self) -> list[SessionInfo]:
        return [t.to_info() for t in self._sessions.values()]

    async def _tmux_query(self, *args: str) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                _TMUX_CMD, *args,
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

    async def _pane_is_alive(self, pane_id: str) -> bool:
        try:
            await self._tmux_query("has-session", "-t", pane_id)
            return True
        except ProviderError:
            return False

    def _get_tracked(self, session_id: str) -> _TrackedSession:
        tracked = self._sessions.get(session_id)
        if tracked is None:
            raise ProviderError(self.name, f"Unknown session {session_id}")
        return tracked

    def _check_tmux_available(self) -> None:
        if shutil.which(_TMUX_CMD) is None:
            raise ProviderError(self.name, "tmux is not installed or not on PATH")


@dataclass
class _TrackedSession:
    session_id: str
    pane_id: str
    status: SessionStatus

    def to_info(self) -> SessionInfo:
        return SessionInfo(
            session_id=self.session_id,
            status=self.status,
            metadata={"pane_id": self.pane_id},
        )

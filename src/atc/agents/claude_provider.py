"""ClaudeCodeProvider — wraps the existing tmux + PTY approach behind AgentProvider.

Spawns Claude Code sessions in tmux panes, sends prompts via tmux send-keys,
and monitors output through PTY streaming. This is the original ATC approach
wrapped behind the provider abstraction.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from atc.agents.base import (
    OutputChunk,
    PromptResult,
    ProviderError,
    SessionInfo,
    SessionStatus,
)

logger = logging.getLogger(__name__)

_TMUX_CMD = "tmux"


class ClaudeCodeProvider:
    """Agent provider using Claude Code in tmux panes.

    Sessions are spawned as tmux split-window commands. Prompts are delivered
    via ``tmux send-keys``. Status is tracked internally (future: via PTY
    monitor). Output streaming delegates to the terminal/pty_stream module.

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

    async def spawn_session(
        self,
        session_id: str,
        *,
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> SessionInfo:
        if session_id in self._sessions:
            raise ProviderError(self.name, f"Session {session_id} already exists")

        self._check_tmux_available()

        # Build the claude command
        cmd_parts = [self._claude_command]
        env_prefix = ""
        if env:
            env_parts = [f"{k}={v}" for k, v in env.items()]
            env_prefix = " ".join(env_parts) + " "

        shell_cmd = f"{env_prefix}{' '.join(cmd_parts)}"

        # Spawn in a new tmux pane
        tmux_args = [
            _TMUX_CMD,
            "split-window",
            "-h",
            "-t",
            self._tmux_session,
            "-P",  # print pane info
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
            raise ProviderError(self.name, f"tmux split-window failed: {err}")

        pane_id = stdout.decode().strip()
        tracked = _TrackedSession(
            session_id=session_id,
            pane_id=pane_id,
            status=SessionStatus.IDLE,
        )
        self._sessions[session_id] = tracked

        logger.info("Spawned Claude Code session %s in pane %s", session_id, pane_id)
        return tracked.to_info()

    async def send_prompt(self, session_id: str, prompt: str) -> PromptResult:
        tracked = self._get_tracked(session_id)

        # Send text + Enter atomically (no await gap per PATTERNS.md)
        escaped = prompt.replace("'", "'\\''")
        send_cmd = [
            _TMUX_CMD,
            "send-keys",
            "-t",
            tracked.pane_id,
            escaped,
            "Enter",
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *send_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
        except OSError as exc:
            raise ProviderError(self.name, f"send-keys failed: {exc}") from exc

        if proc.returncode != 0:
            err = stderr.decode().strip()
            return PromptResult(
                session_id=session_id,
                accepted=False,
                message=f"tmux send-keys failed: {err}",
            )

        tracked.status = SessionStatus.BUSY
        logger.info("Sent prompt to session %s (pane %s)", session_id, tracked.pane_id)
        return PromptResult(session_id=session_id, accepted=True)

    async def get_status(self, session_id: str) -> SessionInfo:
        tracked = self._get_tracked(session_id)

        # Verify the tmux pane still exists
        check_cmd = [
            _TMUX_CMD,
            "has-session",
            "-t",
            tracked.pane_id,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *check_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except OSError:
            tracked.status = SessionStatus.ERROR
            return tracked.to_info()

        if proc.returncode != 0:
            tracked.status = SessionStatus.STOPPED
        return tracked.to_info()

    async def stream_output(self, session_id: str) -> AsyncIterator[OutputChunk]:
        """Stream output via PTY pipe-pane.

        NOTE: Full implementation depends on the terminal/pty_stream module
        (ATC-5). This provides a basic capture-pane fallback.
        """
        tracked = self._get_tracked(session_id)

        capture_cmd = [
            _TMUX_CMD,
            "capture-pane",
            "-t",
            tracked.pane_id,
            "-p",  # print to stdout
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *capture_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
        except OSError as exc:
            raise ProviderError(self.name, f"capture-pane failed: {exc}") from exc

        content = stdout.decode()
        yield OutputChunk(
            session_id=session_id,
            content=content,
            is_final=True,
        )

    async def stop_session(self, session_id: str) -> None:
        tracked = self._get_tracked(session_id)

        kill_cmd = [
            _TMUX_CMD,
            "kill-pane",
            "-t",
            tracked.pane_id,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *kill_cmd,
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

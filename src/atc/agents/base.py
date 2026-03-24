"""Agent provider base protocol — abstract interface for CLI tool backends.

Defines the contract that all agent providers (Claude Code, OpenCode, etc.)
must implement. The session lifecycle delegates to a provider rather than
hardcoding tmux commands directly.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


class SessionStatus(enum.Enum):
    """Lifecycle status of a provider-managed session."""

    STARTING = "starting"
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass(frozen=True)
class SessionInfo:
    """Snapshot of a session's current state as reported by the provider."""

    session_id: str
    status: SessionStatus
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PromptResult:
    """Result of sending a prompt to a session."""

    session_id: str
    accepted: bool
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OutputChunk:
    """A chunk of streaming output from a session."""

    session_id: str
    content: str
    is_final: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CostModel:
    """Describes the cost structure for a provider."""

    input_cost_per_token: float = 0.0
    output_cost_per_token: float = 0.0
    currency: str = "USD"


@dataclass(frozen=True)
class ProviderCapabilities:
    """Describes what a provider supports."""

    supports_streaming: bool = False
    supports_tool_use: bool = False
    context_window: int = 0
    model: str = ""
    cost_model: CostModel | None = None


@dataclass(frozen=True)
class ProviderMetadata:
    """Metadata about a provider plugin for discoverability."""

    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""


@runtime_checkable
class AgentProvider(Protocol):
    """Protocol defining the interface for agent CLI tool backends.

    Each provider encapsulates one way of running an AI coding agent
    (e.g. Claude Code via tmux+PTY, OpenCode via REST API). The session
    lifecycle layer uses this interface instead of calling tmux directly.
    """

    @property
    def name(self) -> str:
        """Human-readable provider name (e.g. 'claude_code', 'opencode')."""
        ...

    async def spawn_session(
        self,
        session_id: str,
        *,
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
        context_file: Path | None = None,
        role: str = "ace",
    ) -> SessionInfo:
        """Spawn a new agent session.

        Args:
            session_id: Unique identifier for this session.
            working_dir: Working directory for the agent process.
            env: Extra environment variables to pass.
            context_file: Optional path to a CLAUDE.md to copy into working_dir.
            role: Role hint for the session (``tower``, ``leader``, or ``ace``).

        Returns:
            SessionInfo with initial status.

        Raises:
            ProviderError: If the session cannot be spawned.
        """
        ...

    async def prepare_workspace(
        self,
        session_id: str,
        *,
        working_dir: str,
        context_file: Path | None = None,
    ) -> None:
        """Set up the workspace before spawning (mkdir, copy context files).

        Args:
            session_id: Unique identifier for the session being prepared.
            working_dir: Directory to create if it does not exist.
            context_file: Optional CLAUDE.md to copy into working_dir.
                Skipped if working_dir/CLAUDE.md already exists.
        """
        ...

    async def is_ready(self, session_id: str) -> bool:
        """Wait until the agent session is ready to receive instructions.

        Polls the session until it shows an idle prompt.

        Args:
            session_id: Target session identifier.

        Returns:
            True when the session is ready, False if it timed out.
        """
        ...

    async def send_prompt(self, session_id: str, prompt: str) -> PromptResult:
        """Send a prompt/instruction to an existing session.

        Args:
            session_id: Target session identifier.
            prompt: The text prompt to send.

        Returns:
            PromptResult indicating acceptance.

        Raises:
            ProviderError: If the prompt cannot be delivered.
        """
        ...

    async def get_status(self, session_id: str) -> SessionInfo:
        """Get the current status of a session.

        Args:
            session_id: Target session identifier.

        Returns:
            SessionInfo with current status.

        Raises:
            ProviderError: If the session is not found.
        """
        ...

    async def stream_output(self, session_id: str) -> AsyncIterator[OutputChunk]:
        """Stream real-time output from a session.

        Yields OutputChunk objects as the agent produces output.
        The final chunk has ``is_final=True``.

        Args:
            session_id: Target session identifier.

        Yields:
            OutputChunk with content fragments.

        Raises:
            ProviderError: If the stream cannot be established.
        """
        ...

    def get_capabilities(self) -> ProviderCapabilities:
        """Return the capabilities of this provider."""
        ...

    async def stop_session(self, session_id: str) -> None:
        """Stop and clean up a session.

        Args:
            session_id: Target session identifier.

        Raises:
            ProviderError: If the session cannot be stopped cleanly.
        """
        ...

    async def list_sessions(self) -> list[SessionInfo]:
        """List all sessions managed by this provider.

        Returns:
            List of SessionInfo for all known sessions.
        """
        ...


class ProviderError(Exception):
    """Base exception for agent provider errors."""

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        super().__init__(f"[{provider}] {message}")

"""Example provider plugin template."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from atc.agents.base import (
    CostModel,
    OutputChunk,
    PromptResult,
    ProviderCapabilities,
    ProviderError,
    ProviderMetadata,
    SessionInfo,
    SessionStatus,
)

PROVIDER_NAME = "example"
PROVIDER_METADATA = ProviderMetadata(
    name="example",
    version="0.1.0",
    description="Example provider for documentation purposes",
    author="ATC Contributors",
)
LAUNCH_COMMAND = "example-agent"


class ExampleProvider:
    """Minimal provider implementation showing the required interface."""

    def __init__(self, **kwargs: Any) -> None:
        self._sessions: dict[str, SessionStatus] = {}

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_streaming=False,
            supports_tool_use=True,
            context_window=128_000,
            model="example-model",
            cost_model=CostModel(
                input_cost_per_token=0.001,
                output_cost_per_token=0.002,
            ),
        )

    async def spawn_session(
        self,
        session_id: str,
        *,
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> SessionInfo:
        if session_id in self._sessions:
            raise ProviderError(self.name, f"Session {session_id} already exists")
        self._sessions[session_id] = SessionStatus.IDLE
        return SessionInfo(session_id=session_id, status=SessionStatus.IDLE)

    async def send_prompt(self, session_id: str, prompt: str) -> PromptResult:
        if session_id not in self._sessions:
            raise ProviderError(self.name, f"Session {session_id} not found")
        self._sessions[session_id] = SessionStatus.BUSY
        return PromptResult(session_id=session_id, accepted=True)

    async def get_status(self, session_id: str) -> SessionInfo:
        if session_id not in self._sessions:
            raise ProviderError(self.name, f"Session {session_id} not found")
        return SessionInfo(session_id=session_id, status=self._sessions[session_id])

    async def stream_output(self, session_id: str) -> AsyncIterator[OutputChunk]:
        if session_id not in self._sessions:
            raise ProviderError(self.name, f"Session {session_id} not found")
        yield OutputChunk(session_id=session_id, content="", is_final=True)

    async def stop_session(self, session_id: str) -> None:
        if session_id not in self._sessions:
            raise ProviderError(self.name, f"Session {session_id} not found")
        del self._sessions[session_id]

    async def list_sessions(self) -> list[SessionInfo]:
        return [
            SessionInfo(session_id=sid, status=status) for sid, status in self._sessions.items()
        ]


PROVIDER_CLASS = ExampleProvider

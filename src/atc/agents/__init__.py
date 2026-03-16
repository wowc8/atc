"""Agent provider abstraction layer.

Public API::

    from atc.agents import create_provider, AgentProvider, SessionStatus

    provider = create_provider("opencode", base_url="http://localhost:4096")
    info = await provider.spawn_session("worker-1", working_dir="/tmp/repo")
"""

from atc.agents.base import (
    AgentProvider,
    OutputChunk,
    PromptResult,
    ProviderError,
    SessionInfo,
    SessionStatus,
)
from atc.agents.factory import (
    create_provider,
    list_providers,
    register_provider,
)

__all__ = [
    "AgentProvider",
    "OutputChunk",
    "PromptResult",
    "ProviderError",
    "SessionInfo",
    "SessionStatus",
    "create_provider",
    "list_providers",
    "register_provider",
]

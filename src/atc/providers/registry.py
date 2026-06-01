"""Provider runtime registry for tmux-backed ATC provider implementations."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from atc.config import load_settings
from atc.providers.base import ProviderRuntime
from atc.providers.claude_code.runtime import ClaudeCodeRuntime
from atc.providers.codex.runtime import CodexRuntime

ProviderFactory = Callable[..., ProviderRuntime]


@dataclass(frozen=True, slots=True)
class ProviderRuntimeInfo:
    """Registry-owned provider metadata for listing/config surfaces."""

    name: str
    model: str = ""
    supports_streaming: bool = False
    supports_tool_use: bool = False
    context_window: int = 0

_REGISTRY: dict[str, ProviderFactory] = {}
_BUILTINS_REGISTERED = False


class ProviderRegistryError(ValueError):
    """Raised when provider runtime registry operations fail."""


def register_provider_runtime(name: str, factory: ProviderFactory) -> None:
    """Register a provider runtime factory under a stable provider name."""

    if not name:
        raise ProviderRegistryError("Provider name must be non-empty")
    _REGISTRY[name] = factory


def create_provider_runtime(name: str, **kwargs: Any) -> ProviderRuntime:
    """Instantiate a provider runtime by provider name."""

    _register_builtin_provider_runtimes()
    factory = _REGISTRY.get(name)
    if factory is None:
        available = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ProviderRegistryError(
            f"Unknown provider runtime {name!r}. Available: {available}"
        )

    if not kwargs:
        try:
            settings = load_settings()
            if name == "claude_code":
                kwargs = {
                    "tmux_session": settings.agent_provider.tmux_session,
                    "claude_command": settings.agent_provider.claude_command,
                }
            elif name == "codex":
                kwargs = {
                    "tmux_session": settings.agent_provider.tmux_session,
                    "codex_command": settings.agent_provider.codex_command,
                }
        except Exception:
            kwargs = {}

    return factory(**kwargs)


def get_provider_runtime_factory(name: str) -> ProviderFactory | None:
    """Return the registered factory for a provider, if any."""

    _register_builtin_provider_runtimes()
    return _REGISTRY.get(name)


def list_provider_runtimes() -> list[str]:
    """List registered provider runtime names."""

    _register_builtin_provider_runtimes()
    return sorted(_REGISTRY)




def list_provider_runtime_infos() -> list[ProviderRuntimeInfo]:
    """List registry-owned provider metadata for settings and discovery surfaces."""

    infos: list[ProviderRuntimeInfo] = []
    for name in list_provider_runtimes():
        model = ""
        supports_streaming = False
        supports_tool_use = False
        context_window = 0
        factory = get_provider_runtime_factory(name)
        if factory is ClaudeCodeRuntime:
            model = "claude"
            supports_streaming = True
            supports_tool_use = True
            context_window = 200_000
        elif factory is CodexRuntime:
            model = "codex"
            supports_streaming = True
            supports_tool_use = True
            context_window = 200_000
        infos.append(
            ProviderRuntimeInfo(
                name=name,
                model=model,
                supports_streaming=supports_streaming,
                supports_tool_use=supports_tool_use,
                context_window=context_window,
            )
        )
    return infos

def _register_builtin_provider_runtimes() -> None:
    global _BUILTINS_REGISTERED
    if _BUILTINS_REGISTERED:
        return
    register_provider_runtime("claude_code", ClaudeCodeRuntime)
    register_provider_runtime("codex", CodexRuntime)
    _BUILTINS_REGISTERED = True

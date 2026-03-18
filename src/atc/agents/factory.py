"""Agent provider factory — creates providers by name from config.

Provides a registry of available provider implementations and a factory
function for instantiation. Provider selection is driven by project settings.
"""

from __future__ import annotations

import logging
from typing import Any

from atc.agents.base import AgentProvider, ProviderError

logger = logging.getLogger(__name__)

# Type alias for provider constructor callables
ProviderConstructor = type  # Callable that returns an AgentProvider

# Global registry of known provider types
_REGISTRY: dict[str, ProviderConstructor] = {}


def register_provider(name: str, cls: ProviderConstructor) -> None:
    """Register a provider class under the given name.

    Args:
        name: Lookup key (e.g. ``"claude_code"``, ``"opencode"``).
        cls: The provider class to instantiate when this name is requested.
    """
    _REGISTRY[name] = cls
    logger.debug("Registered agent provider: %s → %s", name, cls.__name__)


def create_provider(name: str, **kwargs: Any) -> AgentProvider:
    """Create an agent provider instance by name.

    Args:
        name: Registered provider name.
        **kwargs: Passed to the provider constructor.

    Returns:
        An AgentProvider instance.

    Raises:
        ProviderError: If the name is not registered.
    """
    cls = _REGISTRY.get(name)
    if cls is None:
        available = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise ProviderError(
            "factory",
            f"Unknown provider {name!r}. Available: {available}",
        )
    provider: AgentProvider = cls(**kwargs)
    logger.info("Created agent provider: %s (%s)", name, cls.__name__)
    return provider


def list_providers() -> list[str]:
    """Return names of all registered providers."""
    return sorted(_REGISTRY)


def get_provider_class(name: str) -> ProviderConstructor | None:
    """Return the registered class for a provider name, or None."""
    return _REGISTRY.get(name)


# Default launch commands per provider
_LAUNCH_COMMANDS: dict[str, str] = {
    "claude_code": "claude --dangerously-skip-permissions",
    "opencode": "opencode",
}


def get_launch_command(provider_name: str) -> str:
    """Return the shell launch command for a given provider name.

    Falls back to ``claude --dangerously-skip-permissions`` for unknown
    providers so existing behaviour is preserved.
    """
    return _LAUNCH_COMMANDS.get(
        provider_name, _LAUNCH_COMMANDS["claude_code"]
    )


def _register_builtins() -> None:
    """Register the built-in providers (claude_code, opencode)."""
    from atc.agents.claude_provider import ClaudeCodeProvider
    from atc.agents.opencode_provider import OpenCodeProvider

    register_provider("claude_code", ClaudeCodeProvider)
    register_provider("opencode", OpenCodeProvider)


# Auto-register builtins on import
_register_builtins()

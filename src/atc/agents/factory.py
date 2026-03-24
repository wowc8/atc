"""Agent provider factory — creates providers by name from config.

Provides a registry of available provider implementations and a factory
function for instantiation. Provider selection is driven by project settings.

Supports dynamic plugin loading: place a Python module in a providers/
directory that exposes PROVIDER_NAME, PROVIDER_CLASS, and optionally
PROVIDER_METADATA and LAUNCH_COMMAND at module level.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

from atc.agents.base import AgentProvider, ProviderError, ProviderMetadata

logger = logging.getLogger(__name__)

ProviderConstructor = type

_REGISTRY: dict[str, ProviderConstructor] = {}
_METADATA: dict[str, ProviderMetadata] = {}
_LAUNCH_COMMANDS: dict[str, str] = {
    "claude_code": "claude --dangerously-skip-permissions",
    "opencode": "opencode",
}
_SCANNED_DIRS: set[str] = set()


def register_provider(
    name: str,
    cls: ProviderConstructor,
    *,
    metadata: ProviderMetadata | None = None,
    launch_command: str | None = None,
) -> None:
    """Register a provider class under the given name."""
    _REGISTRY[name] = cls
    if metadata is not None:
        _METADATA[name] = metadata
    if launch_command is not None:
        _LAUNCH_COMMANDS[name] = launch_command
    logger.debug("Registered agent provider: %s -> %s", name, cls.__name__)


def create_provider(name: str, **kwargs: Any) -> AgentProvider:
    """Create an agent provider instance by name."""
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


def get_provider_info(name: str) -> ProviderMetadata | None:
    """Return metadata for a registered provider, or None."""
    return _METADATA.get(name)


def get_launch_command(provider_name: str) -> str:
    """Return the shell launch command for a given provider name.

    For the ``claude_code`` provider, if the bundled ``scripts/atc-agent``
    wrapper exists it is returned instead of the bare ``claude`` command so
    that workspace setup is guaranteed before Claude starts.
    """
    if provider_name == "claude_code":
        script = Path(__file__).parent.parent.parent.parent / "scripts" / "atc-agent"
        if script.exists():
            return str(script)
    return _LAUNCH_COMMANDS.get(provider_name, _LAUNCH_COMMANDS["claude_code"])


def load_plugins(plugin_dir: str | Path) -> list[str]:
    """Scan a directory for provider plugin modules and register them.

    Each plugin is a .py file exposing PROVIDER_NAME and PROVIDER_CLASS.
    Optional: PROVIDER_METADATA, LAUNCH_COMMAND.
    """
    plugin_path = Path(plugin_dir).resolve()
    dir_key = str(plugin_path)

    if dir_key in _SCANNED_DIRS:
        return []

    if not plugin_path.is_dir():
        _SCANNED_DIRS.add(dir_key)
        return []

    loaded: list[str] = []

    for py_file in sorted(plugin_path.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        module_name = f"atc.agents.plugins.{py_file.stem}"

        try:
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                continue

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            provider_name = getattr(module, "PROVIDER_NAME", None)
            provider_class = getattr(module, "PROVIDER_CLASS", None)

            if provider_name is None or provider_class is None:
                del sys.modules[module_name]
                continue

            metadata = getattr(module, "PROVIDER_METADATA", None)
            launch_cmd = getattr(module, "LAUNCH_COMMAND", None)

            register_provider(
                provider_name,
                provider_class,
                metadata=metadata,
                launch_command=launch_cmd,
            )
            loaded.append(provider_name)
            logger.info("Loaded plugin: %s from %s", provider_name, py_file.name)

        except Exception:
            logger.exception("Failed to load plugin: %s", py_file.name)
            if module_name in sys.modules:
                del sys.modules[module_name]

    _SCANNED_DIRS.add(dir_key)
    return loaded


def _register_builtins() -> None:
    """Register the built-in providers (claude_code, opencode)."""
    from atc.agents.claude_provider import ClaudeCodeProvider
    from atc.agents.opencode_provider import OpenCodeProvider

    register_provider(
        "claude_code",
        ClaudeCodeProvider,
        metadata=ProviderMetadata(
            name="claude_code",
            version="1.0.0",
            description="Claude Code via tmux panes",
            author="ATC",
        ),
    )
    register_provider(
        "opencode",
        OpenCodeProvider,
        metadata=ProviderMetadata(
            name="opencode",
            version="1.0.0",
            description="OpenCode via REST API",
            author="ATC",
        ),
    )


_register_builtins()

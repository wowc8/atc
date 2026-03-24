"""Agent authentication helpers — API key resolution and OAuth detection."""

from __future__ import annotations

import os
from typing import Literal

_OAUTH_KEY_PREFIXES = ("oat", "claude_")


def is_oauth_key(key: str) -> bool:
    """Return True if *key* is an OAuth token rather than a real API key."""
    return any(key.startswith(prefix) for prefix in _OAUTH_KEY_PREFIXES)


def resolve_agent_api_key() -> str | None:
    """Return the API key to use for spawned agent processes.

    Resolution order:
    1. ``ATC_ANTHROPIC_API_KEY`` — dedicated key for agent sessions
    2. ``ANTHROPIC_API_KEY`` — fallback (may be an OAuth token)

    Returns ``None`` if neither is set.
    """
    key = os.environ.get("ATC_ANTHROPIC_API_KEY")
    if key:
        return key
    return os.environ.get("ANTHROPIC_API_KEY") or None


def get_auth_mode() -> Literal["api_key", "oauth", "none"]:
    """Return the effective authentication mode.

    - ``'api_key'``: ``ATC_ANTHROPIC_API_KEY`` is set and is a real API key
    - ``'oauth'``: active key is an OAuth token (``oat*`` / ``claude_*`` prefix)
    - ``'none'``: no key configured at all
    """
    key = resolve_agent_api_key()
    if key is None:
        return "none"
    if is_oauth_key(key):
        return "oauth"
    return "api_key"

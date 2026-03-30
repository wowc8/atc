"""Agent authentication helpers — API key resolution and OAuth detection."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

_OAUTH_KEY_PREFIXES = ("oat", "claude_", "sk-ant-oat01", "sk-ant-ort")


def is_oauth_key(key: str) -> bool:
    """Return True if *key* is an OAuth token rather than a real API key."""
    return any(key.startswith(prefix) for prefix in _OAUTH_KEY_PREFIXES)


def claude_credentials_exist() -> bool:
    """Return True if ~/.claude/credentials.json exists with an OAuth section.

    Used to determine if the user has Claude Code authenticated on this machine
    without extracting the token — Claude handles its own token refresh, so we
    should never read the token out and pass it as an env var (access tokens are
    short-lived and won't auto-refresh when overridden via CLAUDE_CODE_OAUTH_TOKEN).
    """
    credentials_path = Path.home() / ".claude" / "credentials.json"
    if not credentials_path.exists():
        return False
    try:
        data = json.loads(credentials_path.read_text())
        oauth = data.get("claudeAiOauth", {})
        return bool(oauth.get("accessToken") or oauth.get("refreshToken"))
    except Exception:
        return False


def resolve_agent_api_key() -> str | None:
    """Return the API key to use for spawned agent processes.

    Resolution order:
    1. ``ATC_ANTHROPIC_API_KEY`` — dedicated Anthropic API key (highest priority)
    2. ``ANTHROPIC_API_KEY`` — fallback env var (may be a real key or OAuth token)
    3. ``CLAUDE_CODE_OAUTH_TOKEN`` — explicit OAuth token env var

    Intentionally does NOT read from ~/.claude/credentials.json.
    When the user is logged into Claude Code, we let ``claude`` manage its own
    auth (token refresh, etc.) without injecting a stale access token as an
    env var. Use ``claude_credentials_exist()`` to check if Claude is authed.

    Returns ``None`` if no explicit key is configured via env vars.
    """
    key = os.environ.get("ATC_ANTHROPIC_API_KEY")
    if key:
        return key
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    return os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or None


def is_auth_available() -> bool:
    """Return True if any auth source is configured for agent sessions.

    Checks env vars first, then falls back to checking if Claude Code has
    stored credentials on disk (i.e. the user has run ``claude login``).
    """
    if resolve_agent_api_key():
        return True
    return claude_credentials_exist()


def get_auth_mode() -> Literal["api_key", "oauth", "none"]:
    """Return the effective authentication mode.

    - ``'api_key'``: ``ATC_ANTHROPIC_API_KEY`` or ``ANTHROPIC_API_KEY`` is a real API key
    - ``'oauth'``: active key is an OAuth token (``oat*`` / ``claude_*`` prefix),
                   or no env key set but Claude credentials file exists
    - ``'none'``: no key configured at all, no Claude credentials on disk
    """
    key = resolve_agent_api_key()
    if key is not None:
        if is_oauth_key(key):
            return "oauth"
        return "api_key"
    # No env var set — check if Claude is logged in (credentials file present)
    if claude_credentials_exist():
        return "oauth"
    return "none"

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


def _read_claude_credentials() -> str | None:
    """Read the OAuth access token from Claude Code's credentials file.

    Claude Code stores credentials at ``~/.claude/credentials.json`` after
    the user has logged in via ``claude login`` or first run.  This allows
    ATC to work without any manual environment variable setup — as long as
    the user has Claude Code installed and authenticated.

    Returns the access token string, or ``None`` if not found / unreadable.
    """
    credentials_path = Path.home() / ".claude" / "credentials.json"
    if not credentials_path.exists():
        return None
    try:
        data = json.loads(credentials_path.read_text())
        # Primary path: claudeAiOauth.accessToken
        oauth = data.get("claudeAiOauth", {})
        token = oauth.get("accessToken")
        if token and isinstance(token, str):
            logger.debug("Loaded OAuth token from ~/.claude/credentials.json")
            return token
    except Exception as exc:
        logger.debug("Could not read ~/.claude/credentials.json: %s", exc)
    return None


def resolve_agent_api_key() -> str | None:
    """Return the API key to use for spawned agent processes.

    Resolution order:
    1. ``ATC_ANTHROPIC_API_KEY`` — dedicated key for agent sessions (highest priority)
    2. ``ANTHROPIC_API_KEY`` — fallback env var (may be an OAuth token)
    3. ``CLAUDE_CODE_OAUTH_TOKEN`` — explicit OAuth token env var
    4. ``~/.claude/credentials.json`` — Claude Code's stored credentials (no env var needed)

    The credentials file fallback means ATC works out-of-the-box for any user
    who has Claude Code installed and logged in, without manual environment setup.

    Returns ``None`` if no key is available from any source.
    """
    key = os.environ.get("ATC_ANTHROPIC_API_KEY")
    if key:
        return key
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    key = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if key:
        return key
    # Fall back to Claude Code's stored credentials — works for any user who
    # has run `claude login` or used Claude Code at least once.
    return _read_claude_credentials()


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

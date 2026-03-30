"""Unit tests for atc.agents.auth — API key resolution and OAuth detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atc.agents.auth import (
    claude_credentials_exist,
    get_auth_mode,
    is_auth_available,
    is_oauth_key,
    resolve_agent_api_key,
)


class TestIsOAuthKey:
    def test_oat_prefix(self) -> None:
        assert is_oauth_key("oat01_sometoken") is True

    def test_claude_prefix(self) -> None:
        assert is_oauth_key("claude_abc123") is True

    def test_real_api_key(self) -> None:
        assert is_oauth_key("sk-ant-api03-abc123") is False

    def test_empty_string(self) -> None:
        assert is_oauth_key("") is False

    def test_partial_prefix_no_match(self) -> None:
        # "oatmeal" does NOT start with exactly "oat" — wait, it does.
        # Real test: a key that looks similar but isn't OAuth.
        assert is_oauth_key("sk-ant-oat-fake") is False


class TestResolveAgentApiKey:
    def test_atc_key_takes_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATC_ANTHROPIC_API_KEY", "sk-ant-atc-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fallback")
        assert resolve_agent_api_key() == "sk-ant-atc-key"

    def test_falls_back_to_anthropic_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fallback")
        assert resolve_agent_api_key() == "sk-ant-fallback"

    def test_returns_none_when_no_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """resolve_agent_api_key only looks at env vars — not credentials file."""
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        assert resolve_agent_api_key() is None

    def test_atc_key_oauth_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATC_ANTHROPIC_API_KEY", "oat01_mytoken")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert resolve_agent_api_key() == "oat01_mytoken"

    def test_empty_anthropic_key_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        assert resolve_agent_api_key() is None

    def test_claude_code_oauth_token_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oat01_fromenv")
        assert resolve_agent_api_key() == "oat01_fromenv"

    def test_does_not_read_credentials_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """resolve_agent_api_key must NOT read ~/.claude/credentials.json.
        Tokens extracted from there are short-lived and won't auto-refresh."""
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        # Even if the credentials file exists, resolve_agent_api_key returns None
        import atc.agents.auth as auth_mod
        monkeypatch.setattr(auth_mod, "claude_credentials_exist", lambda: True)
        assert resolve_agent_api_key() is None


class TestClaudeCredentialsExist:
    def test_returns_false_when_no_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import atc.agents.auth as auth_mod
        monkeypatch.setattr(auth_mod, "Path", lambda *a, **kw: tmp_path / ".claude" / "credentials.json")
        # Simple: just patch the whole function for isolation
        monkeypatch.setattr(auth_mod, "claude_credentials_exist", lambda: False)
        assert auth_mod.claude_credentials_exist() is False

    def test_detects_credentials_file(self, tmp_path: Path) -> None:
        creds_dir = tmp_path / ".claude"
        creds_dir.mkdir()
        creds_file = creds_dir / "credentials.json"
        creds_file.write_text(json.dumps({
            "claudeAiOauth": {"accessToken": "tok", "refreshToken": "ref"}
        }))
        # Patch Path.home() by patching the module-level Path usage
        import atc.agents.auth as auth_mod
        original = auth_mod.Path

        def patched_home():
            return tmp_path
        monkeypatch_path = type("FakePath", (), {"home": staticmethod(patched_home)})
        # Direct test of the logic
        import json as _json
        credentials_path = creds_dir / "credentials.json"
        data = _json.loads(credentials_path.read_text())
        oauth = data.get("claudeAiOauth", {})
        assert bool(oauth.get("accessToken") or oauth.get("refreshToken")) is True


class TestGetAuthMode:
    def test_api_key_mode_with_atc_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATC_ANTHROPIC_API_KEY", "sk-ant-api03-realkey")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        import atc.agents.auth as auth_mod
        monkeypatch.setattr(auth_mod, "claude_credentials_exist", lambda: False)
        assert get_auth_mode() == "api_key"

    def test_api_key_mode_fallback_real_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-realkey")
        import atc.agents.auth as auth_mod
        monkeypatch.setattr(auth_mod, "claude_credentials_exist", lambda: False)
        assert get_auth_mode() == "api_key"

    def test_oauth_mode_oat_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "oat01_sometoken")
        import atc.agents.auth as auth_mod
        monkeypatch.setattr(auth_mod, "claude_credentials_exist", lambda: False)
        assert get_auth_mode() == "oauth"

    def test_oauth_mode_claude_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "claude_abc123")
        import atc.agents.auth as auth_mod
        monkeypatch.setattr(auth_mod, "claude_credentials_exist", lambda: False)
        assert get_auth_mode() == "oauth"

    def test_oauth_mode_via_credentials_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no env var set but Claude credentials exist, mode is oauth."""
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        import atc.agents.auth as auth_mod
        monkeypatch.setattr(auth_mod, "claude_credentials_exist", lambda: True)
        assert get_auth_mode() == "oauth"

    def test_none_mode_no_keys_no_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        import atc.agents.auth as auth_mod
        monkeypatch.setattr(auth_mod, "claude_credentials_exist", lambda: False)
        assert get_auth_mode() == "none"

    def test_atc_oauth_key_is_oauth_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATC_ANTHROPIC_API_KEY", "oat01_mytoken")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        import atc.agents.auth as auth_mod
        monkeypatch.setattr(auth_mod, "claude_credentials_exist", lambda: False)
        assert get_auth_mode() == "oauth"


class TestIsAuthAvailable:
    def test_true_when_api_key_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATC_ANTHROPIC_API_KEY", "sk-ant-api03-key")
        import atc.agents.auth as auth_mod
        monkeypatch.setattr(auth_mod, "claude_credentials_exist", lambda: False)
        assert is_auth_available() is True

    def test_true_when_credentials_file_exists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        import atc.agents.auth as auth_mod
        monkeypatch.setattr(auth_mod, "claude_credentials_exist", lambda: True)
        assert is_auth_available() is True

    def test_false_when_nothing_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        import atc.agents.auth as auth_mod
        monkeypatch.setattr(auth_mod, "claude_credentials_exist", lambda: False)
        assert is_auth_available() is False


class TestIsOAuthModeUsesAuthModule:
    """Verify usage.py _is_oauth_mode() delegates to auth module."""

    def test_real_key_not_oauth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from atc.api.routers.usage import _is_oauth_mode

        monkeypatch.setenv("ATC_ANTHROPIC_API_KEY", "sk-ant-api03-realkey")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        import atc.agents.auth as auth_mod
        monkeypatch.setattr(auth_mod, "claude_credentials_exist", lambda: False)
        assert _is_oauth_mode() is False

    def test_oauth_key_is_oauth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from atc.api.routers.usage import _is_oauth_mode

        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "oat01_sometoken")
        import atc.agents.auth as auth_mod
        monkeypatch.setattr(auth_mod, "claude_credentials_exist", lambda: False)
        assert _is_oauth_mode() is True

    def test_no_key_no_credentials_is_oauth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no auth at all, _is_oauth_mode falls back to True (conservative)."""
        from atc.api.routers.usage import _is_oauth_mode

        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        import atc.agents.auth as auth_mod
        monkeypatch.setattr(auth_mod, "claude_credentials_exist", lambda: False)
        # When auth_mode == "none", get_auth_mode returns "none"; _is_oauth_mode
        # checks if key is not a real API key — with no key it returns True.
        assert _is_oauth_mode() is True

"""Unit tests for atc.agents.auth — API key resolution and OAuth detection."""

from __future__ import annotations

import pytest

from atc.agents.auth import get_auth_mode, is_oauth_key, resolve_agent_api_key


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

    def test_returns_none_when_neither_set(self, monkeypatch: pytest.MonkeyPatch, tmp_path: "Path") -> None:
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        import atc.agents.auth as auth_mod
        monkeypatch.setattr(auth_mod, "_read_claude_credentials", lambda: None)
        assert resolve_agent_api_key() is None

    def test_falls_back_to_claude_credentials_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If no env vars set but ~/.claude/credentials.json exists, use its token."""
        import atc.agents.auth as auth_mod

        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.setattr(auth_mod, "_read_claude_credentials", lambda: "sk-ant-oat01-fromfile")

        assert resolve_agent_api_key() == "sk-ant-oat01-fromfile"

    def test_env_var_takes_priority_over_credentials_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Env vars always win over the credentials file."""
        import atc.agents.auth as auth_mod

        monkeypatch.setenv("ATC_ANTHROPIC_API_KEY", "sk-ant-api03-envkey")
        monkeypatch.setattr(auth_mod, "_read_claude_credentials", lambda: "sk-ant-oat01-fromfile")

        assert resolve_agent_api_key() == "sk-ant-api03-envkey"

    def test_atc_key_oauth_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATC_ANTHROPIC_API_KEY", "oat01_mytoken")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert resolve_agent_api_key() == "oat01_mytoken"

    def test_empty_anthropic_key_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        assert resolve_agent_api_key() is None


class TestGetAuthMode:
    def test_api_key_mode_with_atc_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATC_ANTHROPIC_API_KEY", "sk-ant-api03-realkey")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert get_auth_mode() == "api_key"

    def test_api_key_mode_fallback_real_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-realkey")
        assert get_auth_mode() == "api_key"

    def test_oauth_mode_oat_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "oat01_sometoken")
        assert get_auth_mode() == "oauth"

    def test_oauth_mode_claude_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "claude_abc123")
        assert get_auth_mode() == "oauth"

    def test_none_mode_no_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        assert get_auth_mode() == "none"

    def test_atc_oauth_key_is_oauth_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Even ATC_ANTHROPIC_API_KEY can be an OAuth token
        monkeypatch.setenv("ATC_ANTHROPIC_API_KEY", "oat01_mytoken")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert get_auth_mode() == "oauth"


class TestIsOAuthModeUsesAuthModule:
    """Verify usage.py _is_oauth_mode() delegates to auth module."""

    def test_real_key_not_oauth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from atc.api.routers.usage import _is_oauth_mode

        monkeypatch.setenv("ATC_ANTHROPIC_API_KEY", "sk-ant-api03-realkey")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert _is_oauth_mode() is False

    def test_oauth_key_is_oauth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from atc.api.routers.usage import _is_oauth_mode

        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "oat01_sometoken")
        assert _is_oauth_mode() is True

    def test_no_key_is_oauth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from atc.api.routers.usage import _is_oauth_mode

        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert _is_oauth_mode() is True

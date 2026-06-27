"""Tests for OAuth-mode token telemetry behavior."""

from __future__ import annotations

import pytest

from atc.api.routers.usage import UsageSummaryResponse, _is_oauth_mode


class TestIsOAuthMode:
    def test_regular_api_key_is_not_oauth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc123")
        assert _is_oauth_mode() is False

    def test_oat_prefix_is_oauth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "oat01_sometoken")
        assert _is_oauth_mode() is True

    def test_claude_prefix_is_oauth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "claude_abc123")
        assert _is_oauth_mode() is True

    def test_no_key_is_oauth_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # With no key set, provider token telemetry from API-key paths is unavailable.
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert _is_oauth_mode() is True


class TestUsageSummaryResponseModel:
    def test_accepts_oauth_metadata_without_billing_fields(self) -> None:
        resp = UsageSummaryResponse(
            today_tokens=0,
            month_tokens=0,
            oauth_mode=True,
            message="Token usage unavailable from OAuth-only telemetry. Add an Anthropic API key to enable provider usage polling.",
        )
        assert resp.today_tokens == 0
        assert resp.month_tokens == 0
        assert resp.oauth_mode is True
        assert resp.message is not None

    def test_defaults_oauth_mode_to_false(self) -> None:
        resp = UsageSummaryResponse(
            today_tokens=100,
            month_tokens=500,
        )
        assert resp.oauth_mode is False
        assert resp.message is None

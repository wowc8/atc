"""Tests for OAuth-mode cost tracking (Issue #128)."""

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
        # With no key set, cost tracking is disabled (no real API key).
        monkeypatch.delenv("ATC_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert _is_oauth_mode() is True


class TestUsageSummaryResponseModel:
    def test_accepts_null_costs_in_oauth_mode(self) -> None:
        resp = UsageSummaryResponse(
            today_cost=None,
            month_cost=None,
            today_tokens=0,
            month_tokens=0,
            oauth_mode=True,
            message="Cost tracking unavailable — using OAuth authentication. Add an Anthropic API key to enable.",
        )
        assert resp.today_cost is None
        assert resp.month_cost is None
        assert resp.oauth_mode is True
        assert resp.message is not None

    def test_defaults_oauth_mode_to_false(self) -> None:
        resp = UsageSummaryResponse(
            today_cost=1.23,
            month_cost=4.56,
            today_tokens=100,
            month_tokens=500,
        )
        assert resp.oauth_mode is False
        assert resp.message is None

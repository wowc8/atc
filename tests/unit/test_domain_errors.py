"""Unit tests for domain error classes and FastAPI exception handler."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atc.core.errors import (
    ATCError,
    AuthFailedError,
    BudgetError,
    CreationFailedError,
    GitHubError,
    LimitExceededError,
    NoBudgetSetError,
    RateLimitedError,
    SessionNotFoundError,
    SessionStaleError,
)


class TestErrorHierarchy:
    def test_base_defaults(self) -> None:
        err = ATCError()
        assert err.code == "internal_error"
        assert err.status_code == 500
        assert err.extra == {}

    def test_custom_detail(self) -> None:
        err = ATCError("something broke", extra={"key": "val"})
        assert str(err) == "something broke"
        assert err.detail == "something broke"
        assert err.extra == {"key": "val"}

    def test_session_not_found(self) -> None:
        err = SessionNotFoundError("sess-123 missing")
        assert err.code == "session_not_found"
        assert err.status_code == 404
        assert "sess-123" in err.detail

    def test_session_stale(self) -> None:
        err = SessionStaleError("state changed")
        assert err.code == "session_stale"
        assert err.status_code == 409

    def test_creation_failed(self) -> None:
        err = CreationFailedError()
        assert err.code == "creation_failed"
        assert err.status_code == 500

    def test_budget_limit_exceeded(self) -> None:
        err = LimitExceededError("over budget")
        assert err.code == "budget_limit_exceeded"
        assert err.status_code == 402
        assert isinstance(err, BudgetError)
        assert isinstance(err, ATCError)

    def test_no_budget_set(self) -> None:
        err = NoBudgetSetError()
        assert err.code == "no_budget_set"
        assert err.status_code == 404

    def test_rate_limited_with_retry_after(self) -> None:
        err = RateLimitedError("slow down", retry_after=60)
        assert err.code == "github_rate_limited"
        assert err.status_code == 429
        assert err.extra["retry_after"] == 60
        assert isinstance(err, GitHubError)

    def test_auth_failed(self) -> None:
        err = AuthFailedError("token expired")
        assert err.code == "github_auth_failed"
        assert err.status_code == 401


class TestToDict:
    def test_minimal(self) -> None:
        d = SessionNotFoundError("gone").to_dict()
        assert d == {"error": {"code": "session_not_found", "message": "gone"}}

    def test_with_extra(self) -> None:
        err = RateLimitedError("slow", retry_after=30)
        d = err.to_dict()
        assert d["error"]["extra"]["retry_after"] == 30


@pytest.fixture()
def app_with_handler() -> FastAPI:
    """Minimal FastAPI app with the ATCError handler registered."""
    from fastapi.responses import JSONResponse

    app = FastAPI()

    @app.exception_handler(ATCError)
    async def handler(request: object, exc: ATCError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.get("/not-found")
    async def not_found() -> None:
        raise SessionNotFoundError("sess-abc")

    @app.get("/stale")
    async def stale() -> None:
        raise SessionStaleError("state changed")

    @app.get("/rate-limited")
    async def rate_limited() -> None:
        raise RateLimitedError("GitHub API limit", retry_after=120)

    @app.get("/auth-failed")
    async def auth_failed() -> None:
        raise AuthFailedError("bad token")

    @app.get("/budget")
    async def budget() -> None:
        raise LimitExceededError("over $100 limit")

    return app


class TestExceptionHandler:
    def test_session_not_found_returns_404(self, app_with_handler: FastAPI) -> None:
        client = TestClient(app_with_handler)
        resp = client.get("/not-found")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["code"] == "session_not_found"
        assert "sess-abc" in body["error"]["message"]

    def test_session_stale_returns_409(self, app_with_handler: FastAPI) -> None:
        client = TestClient(app_with_handler)
        resp = client.get("/stale")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "session_stale"

    def test_rate_limited_returns_429(self, app_with_handler: FastAPI) -> None:
        client = TestClient(app_with_handler)
        resp = client.get("/rate-limited")
        assert resp.status_code == 429
        body = resp.json()
        assert body["error"]["code"] == "github_rate_limited"
        assert body["error"]["extra"]["retry_after"] == 120

    def test_auth_failed_returns_401(self, app_with_handler: FastAPI) -> None:
        client = TestClient(app_with_handler)
        resp = client.get("/auth-failed")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "github_auth_failed"

    def test_budget_returns_402(self, app_with_handler: FastAPI) -> None:
        client = TestClient(app_with_handler)
        resp = client.get("/budget")
        assert resp.status_code == 402
        assert resp.json()["error"]["code"] == "budget_limit_exceeded"

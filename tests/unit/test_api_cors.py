"""Desktop webviews must be able to read REST responses, not just reach the server."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from atc.api.app import create_app
from atc.core.health import HealthResult

if TYPE_CHECKING:
    from fastapi import FastAPI

    from atc.config import Settings

DESKTOP_ORIGINS = ("tauri://localhost", "http://tauri.localhost")
UNTRUSTED_ORIGINS = (
    "https://example.com",
    "null",
    "tauri://localhost.evil.test",
    "http://tauri.localhost.evil.test",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


@pytest.fixture
def api_app(settings: Settings) -> FastAPI:
    # Do not run lifespan: no real tmux, DB, or external services needed here.
    app = create_app(settings)
    app.state.health = HealthResult(ok=True, message="smoke test passed", duration_ms=4335.0)
    return app


@pytest.fixture
def client(api_app: FastAPI) -> TestClient:
    return TestClient(api_app)


@pytest.mark.parametrize("origin", DESKTOP_ORIGINS)
@pytest.mark.parametrize("healthy", [True, False])
def test_desktop_can_read_health(
    api_app: FastAPI, client: TestClient, origin: str, healthy: bool
) -> None:
    api_app.state.health.ok = healthy
    response = client.get("/api/health", headers={"Origin": origin})

    assert response.status_code == 200
    assert response.json()["status"] == ("ok" if healthy else "degraded")
    # This is stored smoke duration, not the duration of this GET request.
    assert response.json()["duration_ms"] == 4335.0
    assert response.headers["access-control-allow-origin"] == origin
    assert "Origin" in response.headers["vary"]
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("origin", DESKTOP_ORIGINS)
@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_desktop_json_preflight(client: TestClient, origin: str, method: str) -> None:
    response = client.options(
        "/api/projects",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert method in response.headers["access-control-allow-methods"]
    assert "content-type" in response.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("origin", UNTRUSTED_ORIGINS)
def test_untrusted_origin_cannot_read_health(client: TestClient, origin: str) -> None:
    response = client.get("/api/health", headers={"Origin": origin})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("origin", UNTRUSTED_ORIGINS)
def test_untrusted_origin_preflight_rejected(client: TestClient, origin: str) -> None:
    response = client.options(
        "/api/projects",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    ("method", "header"), [("TRACE", "content-type"), ("POST", "x-untrusted-header")]
)
def test_unneeded_preflight_permissions_rejected(
    client: TestClient, method: str, header: str
) -> None:
    response = client.options(
        "/api/projects",
        headers={
            "Origin": "tauri://localhost",
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": header,
        },
    )

    assert response.status_code == 400


def test_requests_without_origin_unchanged(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "access-control-allow-origin" not in response.headers


def test_desktop_can_read_http_errors(client: TestClient) -> None:
    response = client.get("/api/not-a-route", headers={"Origin": "tauri://localhost"})

    assert response.status_code == 404
    assert response.headers["access-control-allow-origin"] == "tauri://localhost"

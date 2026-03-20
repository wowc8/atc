"""Tests for WebSocket broadcasting on context CRUD operations."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from atc.api.app import create_app
from atc.api.ws.hub import WsHub
from atc.config import Settings

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def broadcasts() -> list[tuple[str, Any]]:
    """Collects (channel, data) tuples from WsHub.broadcast calls."""
    return []


@pytest.fixture
def client(tmp_path: Path, broadcasts: list[tuple[str, Any]]) -> TestClient:
    db_path = str(tmp_path / "test.db")
    settings = Settings(database={"path": db_path})  # type: ignore[arg-type]
    app = create_app(settings)

    with TestClient(app) as c:
        # Patch the instance's broadcast method after lifespan has started
        ws_hub = app.state.ws_hub

        async def _capture_broadcast(channel: str, data: Any) -> None:
            broadcasts.append((channel, data))

        ws_hub.broadcast = _capture_broadcast  # type: ignore[assignment]
        yield c


def _create_project(client: TestClient) -> str:
    resp = client.post("/api/projects", json={"name": "test-project"})
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_session_direct(client: TestClient, project_id: str, session_type: str = "tower") -> str:
    """Create a session directly in the SQLite DB (no REST endpoint for sessions)."""
    import uuid
    from datetime import datetime, timezone

    db_path = client.app.state.settings.database.path  # type: ignore[union-attr]
    sid = str(uuid.uuid4())
    now = datetime.now(tz=timezone.utc).isoformat()

    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO sessions (id, project_id, session_type, name, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'idle', ?, ?)""",
        (sid, project_id, session_type, f"{session_type}-1", now, now),
    )
    conn.commit()
    conn.close()
    return sid


# ---------------------------------------------------------------------------
# Tests — Global context broadcasts
# ---------------------------------------------------------------------------


class TestGlobalContextBroadcast:
    def test_create_global_broadcasts(self, client: TestClient, broadcasts: list) -> None:
        resp = client.post(
            "/api/context",
            json={"key": "standards", "value": "PEP8"},
        )
        assert resp.status_code == 201
        entry = resp.json()

        # Filter to context broadcasts only (other broadcasts may occur)
        ctx = [(ch, d) for ch, d in broadcasts if ch.startswith("context:")]
        assert len(ctx) == 1
        channel, data = ctx[0]
        assert channel == "context:global"
        assert data["action"] == "created"
        assert data["entry"]["id"] == entry["id"]
        assert data["entry"]["key"] == "standards"

    def test_update_global_broadcasts(self, client: TestClient, broadcasts: list) -> None:
        resp = client.post(
            "/api/context",
            json={"key": "standards", "value": "PEP8"},
        )
        entry_id = resp.json()["id"]
        broadcasts.clear()

        resp = client.put(
            f"/api/context/{entry_id}",
            json={"value": "black + ruff"},
        )
        assert resp.status_code == 200

        ctx = [(ch, d) for ch, d in broadcasts if ch.startswith("context:")]
        assert len(ctx) == 1
        channel, data = ctx[0]
        assert channel == "context:global"
        assert data["action"] == "updated"
        assert data["entry"]["value"] == "black + ruff"

    def test_delete_global_broadcasts(self, client: TestClient, broadcasts: list) -> None:
        resp = client.post(
            "/api/context",
            json={"key": "temp", "value": "delete-me"},
        )
        entry_id = resp.json()["id"]
        broadcasts.clear()

        resp = client.delete(f"/api/context/{entry_id}")
        assert resp.status_code == 204

        ctx = [(ch, d) for ch, d in broadcasts if ch.startswith("context:")]
        assert len(ctx) == 1
        channel, data = ctx[0]
        assert channel == "context:global"
        assert data["action"] == "deleted"
        assert data["entry"]["id"] == entry_id


# ---------------------------------------------------------------------------
# Tests — Project context broadcasts
# ---------------------------------------------------------------------------


class TestProjectContextBroadcast:
    def test_create_project_broadcasts(self, client: TestClient, broadcasts: list) -> None:
        project_id = _create_project(client)
        broadcasts.clear()

        resp = client.post(
            f"/api/projects/{project_id}/context",
            json={"key": "arch-doc", "value": "monorepo"},
        )
        assert resp.status_code == 201
        entry = resp.json()

        ctx = [(ch, d) for ch, d in broadcasts if ch.startswith("context:")]
        assert len(ctx) == 1
        channel, data = ctx[0]
        assert channel == f"context:{project_id}"
        assert data["action"] == "created"
        assert data["entry"]["id"] == entry["id"]
        assert data["entry"]["scope"] == "project"

    def test_update_project_broadcasts(self, client: TestClient, broadcasts: list) -> None:
        project_id = _create_project(client)
        resp = client.post(
            f"/api/projects/{project_id}/context",
            json={"key": "arch-doc", "value": "monorepo"},
        )
        entry_id = resp.json()["id"]
        broadcasts.clear()

        resp = client.put(
            f"/api/context/{entry_id}",
            json={"value": "polyrepo"},
        )
        assert resp.status_code == 200

        ctx = [(ch, d) for ch, d in broadcasts if ch.startswith("context:")]
        assert len(ctx) == 1
        channel, data = ctx[0]
        assert channel == f"context:{project_id}"
        assert data["action"] == "updated"

    def test_delete_project_broadcasts(self, client: TestClient, broadcasts: list) -> None:
        project_id = _create_project(client)
        resp = client.post(
            f"/api/projects/{project_id}/context",
            json={"key": "temp", "value": "x"},
        )
        entry_id = resp.json()["id"]
        broadcasts.clear()

        resp = client.delete(f"/api/context/{entry_id}")
        assert resp.status_code == 204

        ctx = [(ch, d) for ch, d in broadcasts if ch.startswith("context:")]
        assert len(ctx) == 1
        channel, data = ctx[0]
        assert channel == f"context:{project_id}"
        assert data["action"] == "deleted"


# ---------------------------------------------------------------------------
# Tests — Session context broadcasts
# ---------------------------------------------------------------------------


class TestSessionContextBroadcast:
    def test_create_session_broadcasts(self, client: TestClient, broadcasts: list) -> None:
        project_id = _create_project(client)
        session_id = _create_session_direct(client, project_id, "tower")
        broadcasts.clear()

        resp = client.post(
            f"/api/sessions/{session_id}/context",
            json={"scope": "tower", "key": "memo", "value": "remember this"},
        )
        assert resp.status_code == 201
        entry = resp.json()

        ctx = [(ch, d) for ch, d in broadcasts if ch.startswith("context:")]
        assert len(ctx) == 1
        channel, data = ctx[0]
        assert channel == f"context:session:{session_id}"
        assert data["action"] == "created"
        assert data["entry"]["id"] == entry["id"]

    def test_update_session_broadcasts(self, client: TestClient, broadcasts: list) -> None:
        project_id = _create_project(client)
        session_id = _create_session_direct(client, project_id, "tower")
        resp = client.post(
            f"/api/sessions/{session_id}/context",
            json={"scope": "tower", "key": "memo", "value": "v1"},
        )
        entry_id = resp.json()["id"]
        broadcasts.clear()

        resp = client.put(
            f"/api/context/{entry_id}",
            json={"value": "v2"},
        )
        assert resp.status_code == 200

        ctx = [(ch, d) for ch, d in broadcasts if ch.startswith("context:")]
        assert len(ctx) == 1
        channel, data = ctx[0]
        assert channel == f"context:session:{session_id}"
        assert data["action"] == "updated"

    def test_delete_session_broadcasts(self, client: TestClient, broadcasts: list) -> None:
        project_id = _create_project(client)
        session_id = _create_session_direct(client, project_id, "tower")
        resp = client.post(
            f"/api/sessions/{session_id}/context",
            json={"scope": "tower", "key": "memo", "value": "gone"},
        )
        entry_id = resp.json()["id"]
        broadcasts.clear()

        resp = client.delete(f"/api/context/{entry_id}")
        assert resp.status_code == 204

        ctx = [(ch, d) for ch, d in broadcasts if ch.startswith("context:")]
        assert len(ctx) == 1
        channel, data = ctx[0]
        assert channel == f"context:session:{session_id}"
        assert data["action"] == "deleted"


# ---------------------------------------------------------------------------
# Tests — No broadcast on read-only operations
# ---------------------------------------------------------------------------


class TestNoBroadcastOnReads:
    def test_list_global_no_broadcast(self, client: TestClient, broadcasts: list) -> None:
        client.post("/api/context", json={"key": "x", "value": "y"})
        broadcasts.clear()

        resp = client.get("/api/context")
        assert resp.status_code == 200
        ctx = [(ch, d) for ch, d in broadcasts if ch.startswith("context:")]
        assert len(ctx) == 0

    def test_get_single_no_broadcast(self, client: TestClient, broadcasts: list) -> None:
        resp = client.post("/api/context", json={"key": "x", "value": "y"})
        entry_id = resp.json()["id"]
        broadcasts.clear()

        resp = client.get(f"/api/context/{entry_id}")
        assert resp.status_code == 200
        ctx = [(ch, d) for ch, d in broadcasts if ch.startswith("context:")]
        assert len(ctx) == 0


# ---------------------------------------------------------------------------
# Tests — No broadcast on errors
# ---------------------------------------------------------------------------


class TestNoBroadcastOnErrors:
    def test_update_nonexistent_no_broadcast(self, client: TestClient, broadcasts: list) -> None:
        broadcasts.clear()
        resp = client.put(
            "/api/context/nonexistent-id",
            json={"value": "nope"},
        )
        assert resp.status_code == 404
        ctx = [(ch, d) for ch, d in broadcasts if ch.startswith("context:")]
        assert len(ctx) == 0

    def test_delete_nonexistent_no_broadcast(self, client: TestClient, broadcasts: list) -> None:
        broadcasts.clear()
        resp = client.delete("/api/context/nonexistent-id")
        assert resp.status_code == 404
        ctx = [(ch, d) for ch, d in broadcasts if ch.startswith("context:")]
        assert len(ctx) == 0


# ---------------------------------------------------------------------------
# Tests — Channel routing logic
# ---------------------------------------------------------------------------


class TestChannelRouting:
    def test_global_channel(self, client: TestClient, broadcasts: list) -> None:
        """Global entries broadcast on 'context:global'."""
        client.post("/api/context", json={"key": "g1", "value": "v"})
        ctx = [(ch, d) for ch, d in broadcasts if ch.startswith("context:")]
        assert ctx[0][0] == "context:global"

    def test_project_channel_uses_project_id(self, client: TestClient, broadcasts: list) -> None:
        """Project entries broadcast on 'context:{project_id}'."""
        pid = _create_project(client)
        broadcasts.clear()
        client.post(f"/api/projects/{pid}/context", json={"key": "p1", "value": "v"})
        ctx = [(ch, d) for ch, d in broadcasts if ch.startswith("context:")]
        assert ctx[0][0] == f"context:{pid}"

    def test_session_channel_uses_session_id(self, client: TestClient, broadcasts: list) -> None:
        """Session entries broadcast on 'context:session:{session_id}'."""
        pid = _create_project(client)
        sid = _create_session_direct(client, pid, "tower")
        broadcasts.clear()
        client.post(
            f"/api/sessions/{sid}/context",
            json={"scope": "tower", "key": "s1", "value": "v"},
        )
        ctx = [(ch, d) for ch, d in broadcasts if ch.startswith("context:")]
        assert ctx[0][0] == f"context:session:{sid}"


# ---------------------------------------------------------------------------
# Tests — Broadcast payload structure
# ---------------------------------------------------------------------------


class TestBroadcastPayload:
    def test_payload_has_action_and_entry(self, client: TestClient, broadcasts: list) -> None:
        resp = client.post("/api/context", json={"key": "k", "value": "v"})
        entry = resp.json()

        ctx = [(ch, d) for ch, d in broadcasts if ch.startswith("context:")]
        _, data = ctx[0]
        assert "action" in data
        assert "entry" in data
        assert data["entry"]["id"] == entry["id"]
        assert data["entry"]["key"] == entry["key"]
        assert data["entry"]["value"] == entry["value"]
        assert data["entry"]["scope"] == entry["scope"]

    def test_deleted_payload_includes_full_entry(self, client: TestClient, broadcasts: list) -> None:
        resp = client.post("/api/context", json={"key": "k", "value": "v"})
        entry = resp.json()
        broadcasts.clear()

        client.delete(f"/api/context/{entry['id']}")
        ctx = [(ch, d) for ch, d in broadcasts if ch.startswith("context:")]
        _, data = ctx[0]
        assert data["action"] == "deleted"
        assert data["entry"]["id"] == entry["id"]
        assert data["entry"]["key"] == "k"


# ---------------------------------------------------------------------------
# Tests — No hub available (graceful degradation)
# ---------------------------------------------------------------------------


class TestNoHubGraceful:
    def test_create_works_without_hub(self, tmp_path: "Path") -> None:
        """CRUD operations succeed even if ws_hub is not set on app.state."""
        db_path = str(tmp_path / "test.db")
        settings = Settings(database={"path": db_path})  # type: ignore[arg-type]
        app = create_app(settings)
        with TestClient(app) as c:
            # Remove the hub to simulate no WebSocket layer
            del app.state.ws_hub

            resp = c.post("/api/context", json={"key": "no-hub", "value": "ok"})
            assert resp.status_code == 201

            entry_id = resp.json()["id"]
            resp = c.put(f"/api/context/{entry_id}", json={"value": "updated"})
            assert resp.status_code == 200

            resp = c.delete(f"/api/context/{entry_id}")
            assert resp.status_code == 204

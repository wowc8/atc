"""E2E smoke tests for core ATC workflows.

Covers: health, project CRUD, leader lifecycle, tower status,
and WebSocket connectivity — all through the REST/WS API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from atc.api.app import create_app
from atc.config import Settings

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db_path = str(tmp_path / "test.db")
    settings = Settings(database={"path": db_path})  # type: ignore[arg-type]
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "version" in data


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------


class TestProjectCRUD:
    def test_create_project(self, client: TestClient) -> None:
        resp = client.post("/api/projects", json={"name": "my-project"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "my-project"
        assert data["status"] == "active"
        assert "id" in data

    def test_create_project_with_optional_fields(self, client: TestClient) -> None:
        resp = client.post(
            "/api/projects",
            json={
                "name": "full-project",
                "description": "A test project",
                "repo_path": "/tmp/repo",
                "github_repo": "org/repo",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["description"] == "A test project"
        assert data["repo_path"] == "/tmp/repo"
        assert data["github_repo"] == "org/repo"

    def test_create_project_name_only(self, client: TestClient) -> None:
        """Name is the only required field — all others optional."""
        resp = client.post("/api/projects", json={"name": "minimal"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["description"] is None
        assert data["repo_path"] is None
        assert data["github_repo"] is None

    def test_list_projects(self, client: TestClient) -> None:
        client.post("/api/projects", json={"name": "proj-a"})
        client.post("/api/projects", json={"name": "proj-b"})
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        names = {p["name"] for p in resp.json()}
        assert {"proj-a", "proj-b"} <= names

    def test_get_project(self, client: TestClient) -> None:
        resp = client.post("/api/projects", json={"name": "detail-proj"})
        project_id = resp.json()["id"]
        resp = client.get(f"/api/projects/{project_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "detail-proj"

    def test_get_nonexistent_project(self, client: TestClient) -> None:
        resp = client.get("/api/projects/nonexistent-id")
        assert resp.status_code == 404

    def test_create_project_auto_creates_leader(self, client: TestClient) -> None:
        """Creating a project should automatically create a Leader row."""
        resp = client.post("/api/projects", json={"name": "auto-leader"})
        project_id = resp.json()["id"]
        resp = client.get(f"/api/projects/{project_id}/manager")
        assert resp.status_code == 200
        leader = resp.json()
        assert leader["project_id"] == project_id
        assert leader["status"] == "idle"


# ---------------------------------------------------------------------------
# Leader lifecycle
# ---------------------------------------------------------------------------


@patch("atc.session.ace._tmux_run", new_callable=AsyncMock)
@patch("atc.leader.leader._ensure_tmux_session", new_callable=AsyncMock)
@patch("atc.leader.leader._spawn_pane", new_callable=AsyncMock, return_value="%1")
@patch("atc.leader.leader._send_keys", new_callable=AsyncMock)
class TestLeaderLifecycle:
    def test_start_leader(
        self,
        mock_send: AsyncMock,
        mock_spawn: AsyncMock,
        mock_ensure: AsyncMock,
        mock_tmux: AsyncMock,
        client: TestClient,
    ) -> None:
        resp = client.post("/api/projects", json={"name": "leader-proj"})
        project_id = resp.json()["id"]

        resp = client.post(
            f"/api/projects/{project_id}/leader/start",
            json={"goal": "Build something great"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert "session_id" in data

    def test_start_and_stop_leader(
        self,
        mock_send: AsyncMock,
        mock_spawn: AsyncMock,
        mock_ensure: AsyncMock,
        mock_tmux: AsyncMock,
        client: TestClient,
    ) -> None:
        resp = client.post("/api/projects", json={"name": "stop-proj"})
        project_id = resp.json()["id"]

        client.post(
            f"/api/projects/{project_id}/leader/start",
            json={"goal": "Test goal"},
        )

        resp = client.post(f"/api/projects/{project_id}/leader/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"

        # Leader should be idle after stopping
        resp = client.get(f"/api/projects/{project_id}/manager")
        assert resp.json()["status"] == "idle"

    def test_send_leader_message(
        self,
        mock_send: AsyncMock,
        mock_spawn: AsyncMock,
        mock_ensure: AsyncMock,
        mock_tmux: AsyncMock,
        client: TestClient,
    ) -> None:
        resp = client.post("/api/projects", json={"name": "msg-proj"})
        project_id = resp.json()["id"]

        client.post(
            f"/api/projects/{project_id}/leader/start",
            json={"goal": "Accept messages"},
        )

        resp = client.post(
            f"/api/projects/{project_id}/leader/message",
            json={"message": "Hello leader"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "sent"
        mock_send.assert_called()

    def test_message_without_active_leader(
        self,
        mock_send: AsyncMock,
        mock_spawn: AsyncMock,
        mock_ensure: AsyncMock,
        mock_tmux: AsyncMock,
        client: TestClient,
    ) -> None:
        resp = client.post("/api/projects", json={"name": "no-leader-proj"})
        project_id = resp.json()["id"]

        resp = client.post(
            f"/api/projects/{project_id}/leader/message",
            json={"message": "Hello?"},
        )
        assert resp.status_code == 409

    def test_start_leader_idempotent(
        self,
        mock_send: AsyncMock,
        mock_spawn: AsyncMock,
        mock_ensure: AsyncMock,
        mock_tmux: AsyncMock,
        client: TestClient,
    ) -> None:
        """Starting a leader that is already running returns the same session."""
        resp = client.post("/api/projects", json={"name": "idempotent-proj"})
        project_id = resp.json()["id"]

        resp1 = client.post(
            f"/api/projects/{project_id}/leader/start",
            json={"goal": "First start"},
        )
        session_id_1 = resp1.json()["session_id"]

        resp2 = client.post(
            f"/api/projects/{project_id}/leader/start",
            json={"goal": "Second start"},
        )
        session_id_2 = resp2.json()["session_id"]

        assert session_id_1 == session_id_2


# ---------------------------------------------------------------------------
# Tower status
# ---------------------------------------------------------------------------


class TestTowerStatus:
    def test_tower_status_empty(self, client: TestClient) -> None:
        resp = client.get("/api/tower/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["active_projects"] == 0
        assert data["total_sessions"] == 0
        assert data["state"] == "idle"

    def test_tower_status_after_project(self, client: TestClient) -> None:
        client.post("/api/projects", json={"name": "counted-proj"})
        resp = client.get("/api/tower/status")
        data = resp.json()
        assert data["active_projects"] == 1

    @patch("atc.session.ace._tmux_run", new_callable=AsyncMock, return_value="%1")
    def test_tower_status_counts_sessions(
        self, mock_tmux: AsyncMock, client: TestClient,
    ) -> None:
        resp = client.post("/api/projects", json={"name": "session-proj"})
        project_id = resp.json()["id"]
        client.post(f"/api/projects/{project_id}/aces", json={"name": "a1"})

        resp = client.get("/api/tower/status")
        data = resp.json()
        # At least 1 session (the ace; leader session is only created on start)
        assert data["total_sessions"] >= 1

    @patch("atc.leader.leader._send_keys", new_callable=AsyncMock)
    @patch("atc.leader.leader._spawn_pane", new_callable=AsyncMock, return_value="%1")
    @patch("atc.leader.leader._ensure_tmux_session", new_callable=AsyncMock)
    def test_submit_goal(
        self,
        mock_ensure: AsyncMock,
        mock_spawn: AsyncMock,
        mock_send: AsyncMock,
        client: TestClient,
    ) -> None:
        resp = client.post("/api/projects", json={"name": "goal-proj"})
        project_id = resp.json()["id"]

        resp = client.post(
            "/api/tower/goal",
            json={"project_id": project_id, "goal": "Ship v1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["project_id"] == project_id
        assert "session_id" in data
        assert "context_package" in data
        assert data["context_package"]["goal"] == "Ship v1"

    def test_submit_goal_missing_project(self, client: TestClient) -> None:
        resp = client.post(
            "/api/tower/goal",
            json={"project_id": "nonexistent", "goal": "Ship v1"},
        )
        assert resp.status_code == 404

    @patch("atc.leader.leader._send_keys", new_callable=AsyncMock)
    @patch("atc.leader.leader._spawn_pane", new_callable=AsyncMock, return_value="%1")
    @patch("atc.leader.leader._ensure_tmux_session", new_callable=AsyncMock)
    def test_submit_goal_twice_while_managing_succeeds(
        self,
        mock_ensure: AsyncMock,
        mock_spawn: AsyncMock,
        mock_send: AsyncMock,
        client: TestClient,
    ) -> None:
        """Tower in MANAGING state can accept new goals (delegates to Leader)."""
        resp = client.post("/api/projects", json={"name": "busy-proj"})
        project_id = resp.json()["id"]

        resp = client.post(
            "/api/tower/goal",
            json={"project_id": project_id, "goal": "First goal"},
        )
        assert resp.status_code == 200

        resp = client.post(
            "/api/tower/goal",
            json={"project_id": project_id, "goal": "Second goal"},
        )
        assert resp.status_code == 200

    @patch("atc.tower.session.stop_tower_session", new_callable=AsyncMock)
    @patch("atc.leader.leader._kill_pane", new_callable=AsyncMock)
    @patch("atc.leader.leader._send_keys", new_callable=AsyncMock)
    @patch("atc.leader.leader._spawn_pane", new_callable=AsyncMock, return_value="%1")
    @patch("atc.leader.leader._ensure_tmux_session", new_callable=AsyncMock)
    def test_stop_tower(
        self,
        mock_ensure: AsyncMock,
        mock_spawn: AsyncMock,
        mock_send: AsyncMock,
        mock_kill: AsyncMock,
        mock_stop_tower: AsyncMock,
        client: TestClient,
    ) -> None:
        resp = client.post("/api/projects", json={"name": "stop-proj"})
        project_id = resp.json()["id"]

        client.post(
            "/api/tower/goal",
            json={"project_id": project_id, "goal": "Stop me"},
        )

        resp = client.post("/api/tower/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"

        # Tower should be idle again
        resp = client.get("/api/tower/status")
        assert resp.json()["state"] == "idle"


# ---------------------------------------------------------------------------
# WebSocket connectivity
# ---------------------------------------------------------------------------


class TestWebSocket:
    def test_ws_connect_and_subscribe(self, client: TestClient) -> None:
        """Client can connect to /ws and send a subscribe message."""
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"channel": "subscribe", "data": ["state"]})
            ws.close()

    def test_ws_subscribe_terminal_channel(self, client: TestClient) -> None:
        """Client can subscribe to a terminal channel."""
        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {"channel": "subscribe", "data": ["terminal:test-session-id"]}
            )
            ws.close()

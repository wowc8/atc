"""E2E smoke tests for core ATC workflows.

Covers: health, project CRUD, leader lifecycle, tower status,
and WebSocket connectivity, all through the REST/WS API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from atc.api.app import create_app
from atc.config import Settings
from atc.runtime.models import RoleKind, RuntimeDeliveryResult

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db_path = str(tmp_path / "test.db")
    settings = Settings(database={"path": db_path})  # type: ignore[arg-type]
    app = create_app(settings)
    with (
        patch("atc.leader.leader._accept_trust_dialog", new_callable=AsyncMock, return_value=False),
        patch("atc.tower.controller.TowerController.start_session", new_callable=AsyncMock),
        TestClient(app) as c,
    ):
        yield c


class TestHealth:
    def test_health_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in {"ok", "degraded"}
        assert "version" in data


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
        resp = client.post("/api/projects", json={"name": "auto-leader"})
        project_id = resp.json()["id"]
        resp = client.get(f"/api/projects/{project_id}/manager")
        assert resp.status_code == 200
        leader = resp.json()
        assert leader["project_id"] == project_id
        assert leader["status"] == "idle"


@patch("atc.session.ace._tmux_run", new_callable=AsyncMock)
@patch(
    "atc.leader.leader._spawn_provider_session", new_callable=AsyncMock, return_value=("atc", "%1")
)
@patch("atc.runtime.service.RuntimeService.send_instruction", new_callable=AsyncMock)
class TestLeaderLifecycle:
    def test_start_leader(
        self,
        mock_send: AsyncMock,
        mock_spawn_provider: AsyncMock,
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
        assert data["status"] == "queued"
        assert data["delivery_state"] == "queued"
        assert "does not prove" in data["recovery"]
        assert "session_id" in data

    def test_start_and_stop_leader(
        self,
        mock_send: AsyncMock,
        mock_spawn_provider: AsyncMock,
        mock_tmux: AsyncMock,
        client: TestClient,
    ) -> None:
        resp = client.post("/api/projects", json={"name": "stop-proj"})
        project_id = resp.json()["id"]

        client.post(f"/api/projects/{project_id}/leader/start", json={"goal": "Test goal"})

        resp = client.post(f"/api/projects/{project_id}/leader/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"

        resp = client.get(f"/api/projects/{project_id}/manager")
        assert resp.json()["status"] == "idle"

    def test_send_leader_message(
        self,
        mock_send: AsyncMock,
        mock_spawn_provider: AsyncMock,
        mock_tmux: AsyncMock,
        client: TestClient,
    ) -> None:
        resp = client.post("/api/projects", json={"name": "msg-proj"})
        project_id = resp.json()["id"]

        client.post(f"/api/projects/{project_id}/leader/start", json={"goal": "Accept messages"})

        resp = client.post(
            f"/api/projects/{project_id}/leader/message",
            json={"message": "Hello leader"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "submitted"
        assert data["delivery_state"] == "submitted"
        assert "provider acknowledgement" in data["message"]
        mock_send.assert_called()

    def test_send_leader_message_surfaces_blocked_delivery(
        self,
        mock_send: AsyncMock,
        mock_spawn_provider: AsyncMock,
        mock_tmux: AsyncMock,
        client: TestClient,
    ) -> None:
        mock_send.return_value = RuntimeDeliveryResult(
            session_id="session-test",
            provider_name="codex",
            role=RoleKind.LEADER,
            status="blocked",
            stage="interrupted",
            verdict="blocked",
            reason_code="trust_required",
            message="Leader instruction blocked: trust_required",
            trace_id="trace-test",
        )
        resp = client.post("/api/projects", json={"name": "blocked-msg-proj"})
        project_id = resp.json()["id"]

        client.post(f"/api/projects/{project_id}/leader/start", json={"goal": "Accept messages"})
        resp = client.post(
            f"/api/projects/{project_id}/leader/message",
            json={"message": "Hello leader"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "blocked"
        assert data["delivery"]["status"] == "blocked"
        assert data["delivery"]["reason_code"] == "trust_required"

    def test_message_without_active_leader(
        self,
        mock_send: AsyncMock,
        mock_spawn_provider: AsyncMock,
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
        mock_spawn_provider: AsyncMock,
        mock_tmux: AsyncMock,
        client: TestClient,
    ) -> None:
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
        initial = client.get("/api/tower/status").json()["active_projects"]
        client.post("/api/projects", json={"name": "counted-proj"})
        resp = client.get("/api/tower/status")
        data = resp.json()
        assert data["active_projects"] == initial + 1

    @patch("atc.session.ace._tmux_run", new_callable=AsyncMock, return_value="%1")
    def test_tower_status_counts_sessions(self, mock_tmux: AsyncMock, client: TestClient) -> None:
        resp = client.post("/api/projects", json={"name": "session-proj"})
        project_id = resp.json()["id"]
        client.post(f"/api/projects/{project_id}/aces", json={"name": "a1"})

        resp = client.get("/api/tower/status")
        data = resp.json()
        assert data["total_sessions"] >= 1

    @patch("atc.runtime.service.RuntimeService.send_instruction", new_callable=AsyncMock)
    @patch(
        "atc.leader.leader._spawn_provider_session",
        new_callable=AsyncMock,
        return_value=("atc", "%1"),
    )
    def test_submit_goal(
        self,
        mock_spawn_provider: AsyncMock,
        mock_send: AsyncMock,
        client: TestClient,
    ) -> None:
        resp = client.post("/api/projects", json={"name": "goal-proj"})
        project_id = resp.json()["id"]

        resp = client.post("/api/tower/goal", json={"project_id": project_id, "goal": "Ship v1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert data["delivery_state"] == "queued"
        assert "not proof" in data["recovery"]
        assert data["project_id"] == project_id
        assert "session_id" in data
        assert "context_package" in data
        assert data["context_package"]["goal"] == "Ship v1"

    def test_submit_goal_missing_project(self, client: TestClient) -> None:
        resp = client.post("/api/tower/goal", json={"project_id": "nonexistent", "goal": "Ship v1"})
        assert resp.status_code == 404

    @patch("atc.runtime.service.RuntimeService.send_instruction", new_callable=AsyncMock)
    @patch(
        "atc.leader.leader._spawn_provider_session",
        new_callable=AsyncMock,
        return_value=("atc", "%1"),
    )
    def test_submit_goal_twice_while_managing_succeeds(
        self,
        mock_spawn_provider: AsyncMock,
        mock_send: AsyncMock,
        client: TestClient,
    ) -> None:
        resp = client.post("/api/projects", json={"name": "busy-proj"})
        project_id = resp.json()["id"]

        resp = client.post("/api/tower/goal", json={"project_id": project_id, "goal": "First goal"})
        assert resp.status_code == 200

        resp = client.post(
            "/api/tower/goal", json={"project_id": project_id, "goal": "Second goal"}
        )
        assert resp.status_code == 200

    @patch("atc.tower.session.stop_tower_session", new_callable=AsyncMock)
    @patch("atc.runtime.service.RuntimeService.stop_session_record", new_callable=AsyncMock)
    @patch("atc.runtime.service.RuntimeService.send_instruction", new_callable=AsyncMock)
    @patch(
        "atc.leader.leader._spawn_provider_session",
        new_callable=AsyncMock,
        return_value=("atc", "%1"),
    )
    def test_stop_tower(
        self,
        mock_spawn_provider: AsyncMock,
        mock_send: AsyncMock,
        mock_kill: AsyncMock,
        mock_stop_tower: AsyncMock,
        client: TestClient,
    ) -> None:
        resp = client.post("/api/projects", json={"name": "stop-proj"})
        project_id = resp.json()["id"]

        client.post("/api/tower/goal", json={"project_id": project_id, "goal": "Stop me"})

        resp = client.post("/api/tower/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"

        resp = client.get("/api/tower/status")
        assert resp.json()["state"] == "idle"


class TestWebSocket:
    def test_ws_connect_and_subscribe(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"channel": "subscribe", "data": ["state"]})
            ws.close()

    def test_ws_subscribe_terminal_channel(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"channel": "subscribe", "data": ["terminal:abc123"]})
            ws.close()

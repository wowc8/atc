"""End-to-end tests for ace session lifecycle.

Covers the full ace workflow: create → start → message → stop → destroy,
exercised through the REST API with mocked tmux operations.
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
    with TestClient(app) as c:
        yield c


def _smart_tmux_mock(*args: str) -> str:
    """Mock _tmux_run that returns sensible values based on the tmux command."""
    cmd = args[0] if args else ""
    if cmd == "new-window":
        return "%1"
    if cmd == "display-message":
        return "0"  # alternate_on = False (TUI not active)
    if cmd == "capture-pane":
        return "$ echo hello\nhello"
    return ""


@patch("atc.session.ace._tmux_run", new_callable=AsyncMock, side_effect=_smart_tmux_mock)
class TestAceLifecycle:
    """Full ace lifecycle through the REST API."""

    def test_create_ace(self, mock_tmux: AsyncMock, client: TestClient) -> None:
        # First create a project
        resp = client.post("/api/projects", json={"name": "test-project"})
        assert resp.status_code == 201
        project_id = resp.json()["id"]

        # Create an ace
        resp = client.post(
            f"/api/projects/{project_id}/aces",
            json={"name": "ace-1"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "ace-1"
        assert data["session_type"] == "ace"
        assert data["status"] in ("idle", "connecting")

    def test_create_and_list_aces(self, mock_tmux: AsyncMock, client: TestClient) -> None:
        mock_tmux.return_value = "%1"

        resp = client.post("/api/projects", json={"name": "proj"})
        project_id = resp.json()["id"]

        client.post(f"/api/projects/{project_id}/aces", json={"name": "ace-a"})
        client.post(f"/api/projects/{project_id}/aces", json={"name": "ace-b"})

        resp = client.get(f"/api/projects/{project_id}/aces")
        assert resp.status_code == 200
        aces = resp.json()
        assert len(aces) == 2
        names = {a["name"] for a in aces}
        assert names == {"ace-a", "ace-b"}

    @patch("atc.runtime.service.RuntimeService.send_instruction", new_callable=AsyncMock)
    def test_full_lifecycle(
        self, mock_send: AsyncMock, mock_tmux: AsyncMock, client: TestClient
    ) -> None:
        """Create → start → message → stop → destroy."""
        mock_tmux.return_value = "%1"
        mock_send.return_value = RuntimeDeliveryResult(
            session_id="session-test",
            provider_name="claude_code",
            role=RoleKind.ACE,
            status="delivered",
            stage="confirmed_running",
            verdict="accepted",
            reason_code="delivery_unverified",
        )

        resp = client.post("/api/projects", json={"name": "lifecycle-proj"})
        project_id = resp.json()["id"]

        # Create
        resp = client.post(
            f"/api/projects/{project_id}/aces",
            json={"name": "worker-1"},
        )
        assert resp.status_code == 201
        session_id = resp.json()["id"]

        # Start
        resp = client.post(
            f"/api/aces/{session_id}/start",
            json={"instruction": "echo hello"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "delivered"
        assert data["delivery_state"] == "delivered"
        assert data["delivery"]["reason_code"] == "delivery_unverified"

        # Message
        resp = client.post(
            f"/api/aces/{session_id}/message",
            json={"message": "do something"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "submitted"
        assert data["delivery_state"] == "submitted"
        assert "provider acknowledgement" in data["message"]

        # Stop
        resp = client.post(f"/api/aces/{session_id}/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"

        # Destroy
        resp = client.delete(f"/api/aces/{session_id}")
        assert resp.status_code == 204

        # Verify gone
        resp = client.get(f"/api/projects/{project_id}/aces")
        assert resp.json() == []

    def test_create_ace_missing_project(self, mock_tmux: AsyncMock, client: TestClient) -> None:
        resp = client.post(
            "/api/projects/nonexistent/aces",
            json={"name": "ace-1"},
        )
        assert resp.status_code == 404

    def test_start_nonexistent_session(self, mock_tmux: AsyncMock, client: TestClient) -> None:
        resp = client.post(
            "/api/aces/nonexistent/start",
            json={"instruction": "hello"},
        )
        assert resp.status_code == 404

    def test_destroy_nonexistent_session(self, mock_tmux: AsyncMock, client: TestClient) -> None:
        resp = client.delete("/api/aces/nonexistent")
        assert resp.status_code == 404

    @patch("atc.runtime.service.RuntimeService.send_instruction", new_callable=AsyncMock)
    def test_start_ace_surfaces_blocked_delivery(
        self, mock_send: AsyncMock, mock_tmux: AsyncMock, client: TestClient
    ) -> None:
        mock_tmux.return_value = "%1"
        mock_send.return_value = RuntimeDeliveryResult(
            session_id="session-test",
            provider_name="codex",
            role=RoleKind.ACE,
            status="blocked",
            stage="interrupted",
            verdict="blocked",
            reason_code="auth_required",
            message="auth required",
        )

        resp = client.post("/api/projects", json={"name": "blocked-ace-proj"})
        project_id = resp.json()["id"]
        resp = client.post(
            f"/api/projects/{project_id}/aces",
            json={"name": "worker-blocked"},
        )
        session_id = resp.json()["id"]

        resp = client.post(
            f"/api/aces/{session_id}/start",
            json={"instruction": "do blocked work"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "blocked"
        assert data["delivery_state"] == "blocked"
        assert data["delivery"]["reason_code"] == "auth_required"
        assert "inspect Ace runtime" in data["recovery"]

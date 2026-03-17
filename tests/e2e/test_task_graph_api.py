"""E2E tests for task graph REST API.

Covers: CRUD operations, status transitions, error handling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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


def _create_project(client: TestClient) -> str:
    resp = client.post("/api/projects", json={"name": "test-project"})
    assert resp.status_code == 201
    return resp.json()["id"]


class TestTaskGraphCRUD:
    def test_create_task_graph(self, client: TestClient) -> None:
        project_id = _create_project(client)
        resp = client.post(
            f"/api/projects/{project_id}/task-graphs",
            json={"title": "Build feature"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Build feature"
        assert data["status"] == "todo"
        assert data["project_id"] == project_id
        assert "id" in data

    def test_create_with_all_fields(self, client: TestClient) -> None:
        project_id = _create_project(client)
        resp = client.post(
            f"/api/projects/{project_id}/task-graphs",
            json={
                "title": "Deploy",
                "description": "Deploy to prod",
                "assigned_ace_id": "ace-1",
                "dependencies": ["dep-1", "dep-2"],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["description"] == "Deploy to prod"
        assert data["assigned_ace_id"] == "ace-1"
        assert data["dependencies"] == ["dep-1", "dep-2"]

    def test_create_invalid_status(self, client: TestClient) -> None:
        project_id = _create_project(client)
        resp = client.post(
            f"/api/projects/{project_id}/task-graphs",
            json={"title": "Bad", "status": "invalid"},
        )
        assert resp.status_code == 422

    def test_create_project_not_found(self, client: TestClient) -> None:
        resp = client.post(
            "/api/projects/nonexistent/task-graphs",
            json={"title": "Task"},
        )
        assert resp.status_code == 404

    def test_list_task_graphs(self, client: TestClient) -> None:
        project_id = _create_project(client)
        client.post(
            f"/api/projects/{project_id}/task-graphs",
            json={"title": "Task A"},
        )
        client.post(
            f"/api/projects/{project_id}/task-graphs",
            json={"title": "Task B"},
        )
        resp = client.get(f"/api/projects/{project_id}/task-graphs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        titles = {t["title"] for t in data}
        assert titles == {"Task A", "Task B"}

    def test_list_empty(self, client: TestClient) -> None:
        project_id = _create_project(client)
        resp = client.get(f"/api/projects/{project_id}/task-graphs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_project_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/projects/nonexistent/task-graphs")
        assert resp.status_code == 404

    def test_get_task_graph(self, client: TestClient) -> None:
        project_id = _create_project(client)
        create_resp = client.post(
            f"/api/projects/{project_id}/task-graphs",
            json={"title": "Get me"},
        )
        tg_id = create_resp.json()["id"]
        resp = client.get(f"/api/task-graphs/{tg_id}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Get me"

    def test_get_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/task-graphs/nonexistent")
        assert resp.status_code == 404

    def test_update_task_graph(self, client: TestClient) -> None:
        project_id = _create_project(client)
        create_resp = client.post(
            f"/api/projects/{project_id}/task-graphs",
            json={"title": "Old title"},
        )
        tg_id = create_resp.json()["id"]
        resp = client.patch(
            f"/api/task-graphs/{tg_id}",
            json={"title": "New title", "description": "Updated desc"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "New title"
        assert data["description"] == "Updated desc"

    def test_update_not_found(self, client: TestClient) -> None:
        resp = client.patch(
            "/api/task-graphs/nonexistent",
            json={"title": "X"},
        )
        assert resp.status_code == 404

    def test_delete_task_graph(self, client: TestClient) -> None:
        project_id = _create_project(client)
        create_resp = client.post(
            f"/api/projects/{project_id}/task-graphs",
            json={"title": "Delete me"},
        )
        tg_id = create_resp.json()["id"]
        resp = client.delete(f"/api/task-graphs/{tg_id}")
        assert resp.status_code == 204

        # Verify it's gone
        resp = client.get(f"/api/task-graphs/{tg_id}")
        assert resp.status_code == 404

    def test_delete_not_found(self, client: TestClient) -> None:
        resp = client.delete("/api/task-graphs/nonexistent")
        assert resp.status_code == 404


class TestTaskGraphStatusTransitions:
    def test_todo_to_in_progress(self, client: TestClient) -> None:
        project_id = _create_project(client)
        create_resp = client.post(
            f"/api/projects/{project_id}/task-graphs",
            json={"title": "Transition test"},
        )
        tg_id = create_resp.json()["id"]
        resp = client.patch(
            f"/api/task-graphs/{tg_id}/status",
            json={"status": "in_progress"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

    def test_full_lifecycle(self, client: TestClient) -> None:
        project_id = _create_project(client)
        create_resp = client.post(
            f"/api/projects/{project_id}/task-graphs",
            json={"title": "Lifecycle"},
        )
        tg_id = create_resp.json()["id"]

        # todo → in_progress
        resp = client.patch(
            f"/api/task-graphs/{tg_id}/status",
            json={"status": "in_progress"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

        # in_progress → done
        resp = client.patch(
            f"/api/task-graphs/{tg_id}/status",
            json={"status": "done"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "done"

    def test_invalid_transition_same_status(self, client: TestClient) -> None:
        project_id = _create_project(client)
        create_resp = client.post(
            f"/api/projects/{project_id}/task-graphs",
            json={"title": "Same status"},
        )
        tg_id = create_resp.json()["id"]
        resp = client.patch(
            f"/api/task-graphs/{tg_id}/status",
            json={"status": "todo"},
        )
        assert resp.status_code == 422

    def test_invalid_status_value(self, client: TestClient) -> None:
        project_id = _create_project(client)
        create_resp = client.post(
            f"/api/projects/{project_id}/task-graphs",
            json={"title": "Bad status"},
        )
        tg_id = create_resp.json()["id"]
        resp = client.patch(
            f"/api/task-graphs/{tg_id}/status",
            json={"status": "invalid"},
        )
        assert resp.status_code == 422

    def test_status_transition_not_found(self, client: TestClient) -> None:
        resp = client.patch(
            "/api/task-graphs/nonexistent/status",
            json={"status": "done"},
        )
        assert resp.status_code == 404

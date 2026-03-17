"""Tests for Leader REST API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atc.core.events import EventBus
from atc.leader.decomposer import DecompositionResult, TaskSpec
from atc.leader.orchestrator import AceAssignment, LeaderOrchestrator
from atc.state.db import (
    _SCHEMA_SQL,
    create_leader,
    create_project,
    create_task_graph,
    get_connection,
    run_migrations,
    update_task_graph_status,
)
from atc.state.models import TaskGraph

# We need to reset the module-level orchestrator cache between tests
from atc.api.routers import leader as leader_module


@pytest.fixture
async def db():
    """In-memory database with schema applied."""
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture(autouse=True)
def clear_orchestrator_cache():
    """Clear the orchestrator cache before each test."""
    leader_module._orchestrators.clear()
    yield
    leader_module._orchestrators.clear()


@pytest.fixture
async def project_with_leader(db):
    """Create project + leader, return (project, leader)."""
    project = await create_project(db, "test-proj", repo_path="/tmp/repo")
    leader = await create_leader(db, project.id, goal="Build auth system")
    return project, leader


@pytest.fixture
def mock_request(db, event_bus):
    """Create a mock FastAPI request with app state."""
    request = MagicMock()
    request.app.state.db = db
    request.app.state.event_bus = event_bus
    return request


# ---------------------------------------------------------------------------
# decompose endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDecomposeEndpoint:
    async def test_decompose_creates_task_graphs(
        self, db, project_with_leader, mock_request,
    ) -> None:
        from atc.api.routers.leader import DecomposeRequest, decompose

        project, leader = project_with_leader
        body = DecomposeRequest(
            task_specs=[
                {"title": "Task A", "description": "First task"},
                {"title": "Task B", "description": "Second task"},
            ],
        )

        result = await decompose(project.id, body, mock_request)

        assert result.error is None
        assert result.project_id == project.id
        assert len(result.task_graphs) == 2

    async def test_decompose_no_leader_returns_404(
        self, db, mock_request,
    ) -> None:
        from atc.api.routers.leader import DecomposeRequest, decompose
        from fastapi import HTTPException

        project = await create_project(db, "orphan-proj")
        body = DecomposeRequest(task_specs=[{"title": "Task A"}])

        with pytest.raises(HTTPException) as exc_info:
            await decompose(project.id, body, mock_request)
        assert exc_info.value.status_code == 404

    async def test_decompose_empty_specs_returns_422(
        self, db, project_with_leader, mock_request,
    ) -> None:
        from atc.api.routers.leader import DecomposeRequest, decompose
        from fastapi import HTTPException

        project, _ = project_with_leader
        body = DecomposeRequest(task_specs=[])

        with pytest.raises(HTTPException) as exc_info:
            await decompose(project.id, body, mock_request)
        assert exc_info.value.status_code == 422

    async def test_decompose_with_dependencies(
        self, db, project_with_leader, mock_request,
    ) -> None:
        from atc.api.routers.leader import DecomposeRequest, decompose

        project, _ = project_with_leader
        body = DecomposeRequest(
            task_specs=[
                {"title": "Base"},
                {"title": "Derived", "dependencies": ["Base"]},
            ],
        )

        result = await decompose(project.id, body, mock_request)

        assert len(result.task_graphs) == 2
        derived = next(t for t in result.task_graphs if t["title"] == "Derived")
        assert derived["dependencies"] is not None


# ---------------------------------------------------------------------------
# spawn-aces endpoint
# ---------------------------------------------------------------------------


@patch("atc.leader.orchestrator.create_ace", new_callable=AsyncMock, return_value="ace-1")
@pytest.mark.asyncio
class TestSpawnAcesEndpoint:
    async def test_spawn_returns_assignments(
        self, mock_create: AsyncMock, db, project_with_leader, mock_request,
    ) -> None:
        from atc.api.routers.leader import spawn_aces

        project, _ = project_with_leader
        await create_task_graph(db, project.id, "Ready Task")

        result = await spawn_aces(project.id, mock_request)

        assert len(result.spawned) == 1
        assert result.spawned[0]["task_title"] == "Ready Task"

    async def test_spawn_with_no_ready_tasks(
        self, mock_create: AsyncMock, db, project_with_leader, mock_request,
    ) -> None:
        from atc.api.routers.leader import spawn_aces

        project, _ = project_with_leader
        # Create task with unmet dependency
        tg1 = await create_task_graph(db, project.id, "Blocker")
        await create_task_graph(db, project.id, "Blocked", dependencies=[tg1.id])

        # First spawn gets the blocker
        await spawn_aces(project.id, mock_request)

        # No more ready tasks (blocked one still waiting)
        result = await spawn_aces(project.id, mock_request)
        # Blocker is already assigned, blocked is waiting
        assert len(result.spawned) == 0


# ---------------------------------------------------------------------------
# progress endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestProgressEndpoint:
    async def test_progress_returns_summary(
        self, db, project_with_leader, mock_request,
    ) -> None:
        from atc.api.routers.leader import get_progress

        project, leader = project_with_leader
        tg1 = await create_task_graph(db, project.id, "Done Task")
        await update_task_graph_status(db, tg1.id, "in_progress")
        await update_task_graph_status(db, tg1.id, "done")
        await create_task_graph(db, project.id, "Todo Task")

        result = await get_progress(project.id, mock_request)

        assert result.total == 2
        assert result.done == 1
        assert result.todo == 1
        assert result.all_done is False
        assert result.leader_id == leader.id


# ---------------------------------------------------------------------------
# task-done and task-failed endpoints
# ---------------------------------------------------------------------------


@patch("atc.leader.orchestrator.destroy_ace", new_callable=AsyncMock)
@patch("atc.leader.orchestrator.create_ace", new_callable=AsyncMock, return_value="ace-1")
@pytest.mark.asyncio
class TestTaskLifecycleEndpoints:
    async def test_task_done(
        self, mock_create: AsyncMock, mock_destroy: AsyncMock,
        db, project_with_leader, mock_request,
    ) -> None:
        from atc.api.routers.leader import TaskDoneRequest, spawn_aces, task_done

        project, _ = project_with_leader
        tg = await create_task_graph(db, project.id, "Finish Me")

        # Spawn ace first
        await spawn_aces(project.id, mock_request)

        body = TaskDoneRequest(task_graph_id=tg.id)
        result = await task_done(project.id, body, mock_request)

        assert result["status"] == "done"

    async def test_task_failed(
        self, mock_create: AsyncMock, mock_destroy: AsyncMock,
        db, project_with_leader, mock_request,
    ) -> None:
        from atc.api.routers.leader import TaskFailedRequest, spawn_aces, task_failed

        project, _ = project_with_leader
        tg = await create_task_graph(db, project.id, "Fail Me")

        # Spawn ace first
        await spawn_aces(project.id, mock_request)

        body = TaskFailedRequest(task_graph_id=tg.id, reason="Test error")
        result = await task_failed(project.id, body, mock_request)

        assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# cleanup endpoint
# ---------------------------------------------------------------------------


@patch("atc.leader.orchestrator.destroy_ace", new_callable=AsyncMock)
@pytest.mark.asyncio
class TestCleanupEndpoint:
    async def test_cleanup_removes_orchestrator(
        self, mock_destroy: AsyncMock, db, project_with_leader, mock_request,
    ) -> None:
        from atc.api.routers.leader import cleanup

        project, _ = project_with_leader

        result = await cleanup(project.id, mock_request)

        assert result["status"] == "cleaned_up"
        assert project.id not in leader_module._orchestrators

"""Tests for Leader REST API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# We need to reset the module-level orchestrator cache between tests
from atc.api.routers import leader as leader_module
from atc.core.events import EventBus
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
    """Clear the orchestrator cache and global counter before each test."""
    from atc.leader import orchestrator as orch_mod

    leader_module._orchestrators.clear()
    orch_mod._GLOBAL_ACTIVE_ACES = 0
    yield
    leader_module._orchestrators.clear()
    orch_mod._GLOBAL_ACTIVE_ACES = 0


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
    # ws_hub.broadcast must be awaitable (used in decompose endpoint)
    request.app.state.ws_hub = MagicMock()
    request.app.state.ws_hub.broadcast = AsyncMock()
    # settings=None so _get_or_create_orchestrator uses defaults (avoids MagicMock
    # leaking into ResourceGovernor float comparisons)
    request.app.state.settings = None
    request.app.state.tower_controller = MagicMock()
    request.app.state.tower_controller.get_progress = AsyncMock(return_value={})
    return request


# ---------------------------------------------------------------------------
# decompose endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDecomposeEndpoint:
    async def test_decompose_creates_task_graphs(
        self,
        db,
        project_with_leader,
        mock_request,
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
        mock_request.app.state.tower_controller.get_progress.assert_awaited()

    async def test_decompose_no_leader_returns_404(
        self,
        db,
        mock_request,
    ) -> None:
        from fastapi import HTTPException

        from atc.api.routers.leader import DecomposeRequest, decompose

        project = await create_project(db, "orphan-proj")
        body = DecomposeRequest(task_specs=[{"title": "Task A"}])

        with pytest.raises(HTTPException) as exc_info:
            await decompose(project.id, body, mock_request)
        assert exc_info.value.status_code == 404

    async def test_decompose_empty_specs_returns_422(
        self,
        db,
        project_with_leader,
        mock_request,
    ) -> None:
        from fastapi import HTTPException

        from atc.api.routers.leader import DecomposeRequest, decompose

        project, _ = project_with_leader
        body = DecomposeRequest(task_specs=[])

        with pytest.raises(HTTPException) as exc_info:
            await decompose(project.id, body, mock_request)
        assert exc_info.value.status_code == 422

    async def test_decompose_with_dependencies(
        self,
        db,
        project_with_leader,
        mock_request,
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
        self,
        mock_create: AsyncMock,
        db,
        project_with_leader,
        mock_request,
    ) -> None:
        from atc.api.routers.leader import spawn_aces

        project, _ = project_with_leader
        await create_task_graph(db, project.id, "Ready Task")

        result = await spawn_aces(project.id, mock_request)

        assert len(result.spawned) == 1
        assert result.spawned[0]["task_title"] == "Ready Task"
        mock_request.app.state.tower_controller.get_progress.assert_awaited()

    async def test_spawn_with_no_ready_tasks(
        self,
        mock_create: AsyncMock,
        db,
        project_with_leader,
        mock_request,
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

    async def test_assign_task_endpoint_returns_single_assignment(
        self,
        mock_create: AsyncMock,
        db,
        project_with_leader,
        mock_request,
    ) -> None:
        from atc.api.routers.leader import AssignTaskRequest, assign_task

        project, _ = project_with_leader
        tg = await create_task_graph(db, project.id, "Ready Task")

        result = await assign_task(
            project.id,
            AssignTaskRequest(task_graph_id=tg.id),
            mock_request,
        )

        assert result.assigned is not None
        assert result.assigned["task_graph_id"] == tg.id
        assert result.assigned["assignment_id"].endswith(f":{tg.id}")
        assert result.assigned["status"] == "assigned"
        mock_request.app.state.tower_controller.get_progress.assert_awaited()


# ---------------------------------------------------------------------------
# progress endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestProgressEndpoint:
    async def test_progress_returns_summary(
        self,
        db,
        project_with_leader,
        mock_request,
    ) -> None:
        from atc.api.routers.leader import get_progress

        project, leader = project_with_leader
        tg1 = await create_task_graph(db, project.id, "Done Task")
        await update_task_graph_status(db, tg1.id, "assigned")
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
        self,
        mock_create: AsyncMock,
        mock_destroy: AsyncMock,
        db,
        project_with_leader,
        mock_request,
    ) -> None:
        from atc.api.routers.leader import TaskDoneRequest, spawn_aces, task_done

        project, _ = project_with_leader
        tg = await create_task_graph(db, project.id, "Finish Me")

        # Spawn ace first
        await spawn_aces(project.id, mock_request)

        body = TaskDoneRequest(task_graph_id=tg.id)
        result = await task_done(project.id, body, mock_request)

        assert result["status"] == "done"
        mock_request.app.state.tower_controller.get_progress.assert_awaited()

    async def test_task_failed(
        self,
        mock_create: AsyncMock,
        mock_destroy: AsyncMock,
        db,
        project_with_leader,
        mock_request,
    ) -> None:
        from atc.api.routers.leader import TaskFailedRequest, spawn_aces, task_failed

        project, _ = project_with_leader
        tg = await create_task_graph(db, project.id, "Fail Me")

        # Spawn ace first
        await spawn_aces(project.id, mock_request)

        body = TaskFailedRequest(task_graph_id=tg.id, reason="Test error")
        result = await task_failed(project.id, body, mock_request)

        assert result["status"] == "failed"
        mock_request.app.state.tower_controller.get_progress.assert_awaited()


# ---------------------------------------------------------------------------
# cleanup endpoint
# ---------------------------------------------------------------------------


@patch("atc.leader.orchestrator.destroy_ace", new_callable=AsyncMock)
@pytest.mark.asyncio
class TestCleanupEndpoint:
    async def test_cleanup_removes_orchestrator(
        self,
        mock_destroy: AsyncMock,
        db,
        project_with_leader,
        mock_request,
    ) -> None:
        from atc.api.routers.leader import cleanup

        project, _ = project_with_leader

        result = await cleanup(project.id, mock_request)

        assert result["status"] == "cleaned_up"
        assert project.id not in leader_module._orchestrators


@pytest.mark.asyncio
async def test_instruct_returns_delivery_state_and_marks_assignment_working(
    db,
    project_with_leader,
    mock_request,
) -> None:
    from atc.api.routers.leader import InstructRequest, instruct_ace
    from atc.runtime.models import DeliveryState, RoleKind, RuntimeDeliveryResult, RuntimeState
    from atc.state.db import assign_task, create_session, create_task_graph, get_task_assignment

    project, leader = project_with_leader
    task = await create_task_graph(db, project.id, "Truthful Ace work")
    ace = await create_session(
        db,
        project_id=project.id,
        session_type="ace",
        name="ace-truthful-work",
        status="idle",
        provider="codex",
        task_id=task.id,
    )
    idempotency_key = f"{leader.id}:{task.id}"
    assignment, _ = await assign_task(db, task.id, ace.id, idempotency_key)

    orch = LeaderOrchestrator(project.id, leader.id, db, event_bus=mock_request.app.state.event_bus)
    orch.assignments[task.id] = AceAssignment(
        ace_session_id=ace.id,
        task_graph_id=task.id,
        task_title=task.title,
        assignment_id=idempotency_key,
        status="assigned",
    )
    leader_module._orchestrators[project.id] = orch

    delivery = RuntimeDeliveryResult(
        session_id=ace.id,
        provider_name="codex",
        role=RoleKind.ACE,
        status="delivered",
        stage="delivery",
        verdict="confirmed",
        trace_id="trace-phase7",
        message="instruction delivered",
        runtime_state=RuntimeState.ACTIVE,
        delivery_state=DeliveryState.ACCEPTED_ACTIVE,
    )

    with patch("atc.leader.orchestrator.start_ace", new=AsyncMock(return_value=delivery)):
        response = await instruct_ace(
            project.id,
            InstructRequest(task_graph_id=task.id, instruction="Work this task"),
            mock_request,
        )

    assert response["status"] == "delivered"
    assert response["delivery_state"] == "delivered"
    assert response["delivery"]["trace_id"] == "trace-phase7"
    refreshed = await get_task_assignment(db, idempotency_key)
    assert refreshed is not None
    assert refreshed.status == "working"

"""Tower progress runtime-truth summary tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from atc.core.events import EventBus
from atc.state.db import (
    _SCHEMA_SQL,
    assign_task,
    create_leader,
    create_project,
    create_task_graph,
    get_connection,
    run_migrations,
    update_task_assignment_dispatch,
)
from atc.tower.controller import TowerController


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


@patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="leader-sess-1")
@pytest.mark.asyncio
async def test_progress_separates_task_lifecycle_from_runtime_truth(
    mock_start: AsyncMock,
    db,
    event_bus: EventBus,
) -> None:
    project = await create_project(db, "truth-progress")
    await create_leader(db, project.id)
    tower = TowerController(db, event_bus)
    await tower.submit_goal(project.id, "Build runtime truth")

    assigned = await create_task_graph(db, project.id, "Assigned but not verified")
    active = await create_task_graph(db, project.id, "Accepted active")
    await create_task_graph(db, project.id, "Done", status="done")

    await assign_task(db, assigned.id, "ace-unverified", "assign-unverified")
    await assign_task(db, active.id, "ace-active", "assign-active")
    await update_task_assignment_dispatch(
        db,
        "assign-active",
        dispatch_delivery_state="accepted_active",
        dispatch_verified=True,
        last_activity=True,
    )

    progress = await tower.get_progress()

    assert progress["total"] == 3
    assert progress["done"] == 1
    assert progress["task_states"] == {"assigned": 2, "done": 1}
    assert progress["delivery_states"] == {
        "queued_unverified": 1,
        "accepted_active": 1,
        "not_started": 1,
    }
    assert progress["runtime_states"] == {
        "starting": 1,
        "active": 1,
        "complete": 1,
    }
    assert progress["dispatch_unverified"] == 1
    assert progress["blocked"] == 0
    mock_start.assert_called_once()


@patch("atc.tower.controller.start_leader", new_callable=AsyncMock, return_value="leader-sess-1")
@pytest.mark.asyncio
async def test_progress_surfaces_blocked_dispatch_separately(
    mock_start: AsyncMock,
    db,
    event_bus: EventBus,
) -> None:
    project = await create_project(db, "blocked-progress")
    await create_leader(db, project.id)
    tower = TowerController(db, event_bus)
    await tower.submit_goal(project.id, "Build blocker truth")

    task = await create_task_graph(db, project.id, "Blocked dispatch")
    await assign_task(db, task.id, "ace-blocked", "assign-blocked")
    await update_task_assignment_dispatch(
        db,
        "assign-blocked",
        dispatch_delivery_state="blocked",
        dispatch_verified=False,
        blocker_reason="ace_dispatch_failed",
    )

    progress = await tower.get_progress()

    assert progress["task_states"] == {"assigned": 1}
    assert progress["delivery_states"] == {"blocked": 1}
    assert progress["runtime_states"] == {"blocked": 1}
    assert progress["blocked"] == 1
    assert progress["dispatch_unverified"] == 0
    mock_start.assert_called_once()

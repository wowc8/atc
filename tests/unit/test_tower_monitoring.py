"""Tests for Phase 8 Tower monitoring cadence policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from atc.core.events import EventBus
from atc.runtime.health import RuntimeHealth
from atc.runtime.models import RuntimeState
from atc.state.db import _SCHEMA_SQL, get_connection, run_migrations
from atc.tower.controller import TowerController, TowerState
from atc.tower.monitoring import decide_tower_monitoring_cadence


@pytest.fixture
async def db():
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


def test_healthy_leader_recent_activity_backs_off_without_ace_inspection() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    decision = decide_tower_monitoring_cadence(
        {
            "runtime_exists": True,
            "pane_attached": True,
            "runtime_state": RuntimeState.ACTIVE.value,
            "current_blocker": None,
            "last_activity_at": (now - timedelta(seconds=45)).isoformat(),
            "task_graph_state": {"total": 3, "in_progress": 1},
        },
        startup_elapsed_seconds=130,
        now=now,
    )

    assert decision.mode == "leader_backoff"
    assert decision.next_poll_seconds == 600
    assert decision.inspect_aces is False
    assert decision.should_nudge_leader is False
    assert decision.reason == "leader_recently_active"


def test_flat_progress_allows_tower_to_escalate_to_detailed_inspection() -> None:
    decision = decide_tower_monitoring_cadence(
        {
            "runtime_exists": True,
            "pane_attached": True,
            "runtime_state": RuntimeState.READY.value,
            "current_blocker": None,
            "last_activity_at": None,
            "task_graph_state": {"total": 2, "assigned": 2},
        },
        startup_elapsed_seconds=400,
        progress_flat_seconds=700,
    )

    assert decision.mode == "inspect_or_recover"
    assert decision.inspect_aces is True
    assert decision.should_nudge_leader is True
    assert decision.reason == "project_progress_flat_past_threshold"


def test_leader_no_recent_activity_nudges_without_ace_inspection() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    decision = decide_tower_monitoring_cadence(
        {
            "runtime_exists": True,
            "pane_attached": True,
            "runtime_state": RuntimeState.READY.value,
            "current_blocker": None,
            "last_activity_at": (now - timedelta(seconds=650)).isoformat(),
            "task_graph_state": {"total": 2, "assigned": 2},
        },
        startup_elapsed_seconds=400,
        now=now,
    )

    assert decision.mode == "leader_health"
    assert decision.inspect_aces is False
    assert decision.should_nudge_leader is True
    assert decision.reason == "leader_no_recent_activity"


@pytest.mark.asyncio
async def test_tower_startup_verification_uses_leader_health_no_ace_inspection(
    db, event_bus
) -> None:
    project_id = "project-1"
    tower = TowerController(db, event_bus)
    tower._state = TowerState.MANAGING
    tower._leader_session_id = "leader-1"

    healthy = RuntimeHealth(
        role="leader",
        project_id=project_id,
        runtime_exists=True,
        pane_attached=True,
        provider="codex",
        session_id="leader-1",
        runtime_state=RuntimeState.ACTIVE.value,
        last_activity_at=datetime.now(UTC).isoformat(),
        task_graph_state={"total": 1, "in_progress": 1},
    )

    with (
        patch("asyncio.sleep", new=AsyncMock()) as sleep_mock,
        patch(
            "atc.tower.controller.leader_health",
            new=AsyncMock(return_value=healthy),
        ) as health_mock,
        patch(
            "atc.tower.controller.send_leader_message",
            new=AsyncMock(),
        ) as nudge_mock,
    ):
        await tower._verify_leader_started(project_id, "leader-1", "goal")

    sleep_mock.assert_called_once_with(10)
    health_mock.assert_awaited_once_with(db, project_id)
    nudge_mock.assert_not_called()
    assert tower.state == TowerState.MANAGING

"""Unit tests for budget enforcer — status transitions and DB interactions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from atc.core.events import EventBus
from atc.state.db import (
    _SCHEMA_SQL,
    create_project,
    create_session,
    get_connection,
    run_migrations,
    upsert_project_budget,
    write_usage_event,
)
from atc.tracking.budget import BudgetEnforcer


@pytest.fixture
async def db():
    """In-memory database with full schema."""
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def ws_hub() -> MagicMock:
    hub = MagicMock()
    hub.broadcast = AsyncMock()
    return hub


# ---------------------------------------------------------------------------
# _compute_status tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestComputeStatus:
    async def test_no_limits_returns_ok(self, db, event_bus) -> None:
        project = await create_project(db, "proj")
        enforcer = BudgetEnforcer(db, event_bus)
        status = await enforcer._compute_status(project.id, None, None, 0.8)
        assert status == "ok"

    async def test_under_threshold_returns_ok(self, db, event_bus) -> None:
        project = await create_project(db, "proj")
        # Record a small cost — under limit
        await write_usage_event(
            db,
            "ai_cost",
            project_id=project.id,
            cost_usd=5.0,
            input_tokens=1000,
            output_tokens=100,
        )
        enforcer = BudgetEnforcer(db, event_bus)
        status = await enforcer._compute_status(project.id, None, 100.0, 0.8)
        assert status == "ok"

    async def test_above_warn_threshold_returns_warn(self, db, event_bus) -> None:
        project = await create_project(db, "proj")
        # Record cost at 85% of $100 limit
        await write_usage_event(
            db,
            "ai_cost",
            project_id=project.id,
            cost_usd=85.0,
            input_tokens=1000,
            output_tokens=100,
        )
        enforcer = BudgetEnforcer(db, event_bus)
        status = await enforcer._compute_status(project.id, None, 100.0, 0.8)
        assert status == "warn"

    async def test_at_limit_returns_exceeded(self, db, event_bus) -> None:
        project = await create_project(db, "proj")
        # Record cost at 100% of $50 limit
        await write_usage_event(
            db,
            "ai_cost",
            project_id=project.id,
            cost_usd=50.0,
            input_tokens=1000,
            output_tokens=100,
        )
        enforcer = BudgetEnforcer(db, event_bus)
        status = await enforcer._compute_status(project.id, None, 50.0, 0.8)
        assert status == "exceeded"

    async def test_over_limit_returns_exceeded(self, db, event_bus) -> None:
        project = await create_project(db, "proj")
        await write_usage_event(
            db,
            "ai_cost",
            project_id=project.id,
            cost_usd=60.0,
            input_tokens=1000,
            output_tokens=100,
        )
        enforcer = BudgetEnforcer(db, event_bus)
        status = await enforcer._compute_status(project.id, None, 50.0, 0.8)
        assert status == "exceeded"

    async def test_daily_token_limit_warn(self, db, event_bus) -> None:
        project = await create_project(db, "proj")
        # Use 90% of 100k token limit
        await write_usage_event(
            db,
            "ai_cost",
            project_id=project.id,
            cost_usd=0.01,
            input_tokens=80_000,
            output_tokens=10_000,
        )
        enforcer = BudgetEnforcer(db, event_bus)
        status = await enforcer._compute_status(project.id, 100_000, None, 0.8)
        assert status == "warn"


# ---------------------------------------------------------------------------
# Status transition tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestStatusTransitions:
    async def test_ok_to_warn_writes_notification(
        self, db, event_bus, ws_hub
    ) -> None:
        project = await create_project(db, "proj")
        await upsert_project_budget(db, project.id, monthly_cost_limit=100.0)

        enforcer = BudgetEnforcer(db, event_bus, ws_hub=ws_hub)
        await enforcer._transition_status(project.id, "ok", "warn", None, 100.0, 0.8)

        # Notification should be written
        cursor = await db.execute(
            "SELECT * FROM notifications WHERE project_id = ?",
            (project.id,),
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0]["level"] == "warning"

    async def test_ok_to_exceeded_pauses_sessions(
        self, db, event_bus, ws_hub
    ) -> None:
        project = await create_project(db, "proj")
        session = await create_session(
            db, project.id, "ace", "ace-1", status="working"
        )
        await upsert_project_budget(db, project.id, monthly_cost_limit=50.0)

        enforcer = BudgetEnforcer(db, event_bus, ws_hub=ws_hub)
        await enforcer._transition_status(project.id, "ok", "exceeded", None, 50.0, 0.8)

        # Session should be paused
        cursor = await db.execute(
            "SELECT status FROM sessions WHERE id = ?",
            (session.id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["status"] == "paused"

    async def test_ok_to_exceeded_writes_budget_notification(
        self, db, event_bus, ws_hub
    ) -> None:
        project = await create_project(db, "proj")
        await upsert_project_budget(db, project.id, monthly_cost_limit=50.0)

        enforcer = BudgetEnforcer(db, event_bus, ws_hub=ws_hub)
        await enforcer._transition_status(project.id, "ok", "exceeded", None, 50.0, 0.8)

        cursor = await db.execute(
            "SELECT level FROM notifications WHERE project_id = ?",
            (project.id,),
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0]["level"] == "budget"

    async def test_ws_broadcast_on_transition(
        self, db, event_bus, ws_hub
    ) -> None:
        project = await create_project(db, "proj")
        await upsert_project_budget(db, project.id, monthly_cost_limit=100.0)

        enforcer = BudgetEnforcer(db, event_bus, ws_hub=ws_hub)
        await enforcer._transition_status(project.id, "ok", "warn", None, 100.0, 0.8)

        ws_hub.broadcast.assert_called_once()
        channel = ws_hub.broadcast.call_args[0][0]
        assert channel == f"budget:{project.id}"

    async def test_status_updated_in_db(self, db, event_bus, ws_hub) -> None:
        project = await create_project(db, "proj")
        await upsert_project_budget(db, project.id, monthly_cost_limit=100.0)

        enforcer = BudgetEnforcer(db, event_bus, ws_hub=ws_hub)
        await enforcer._transition_status(project.id, "ok", "warn", None, 100.0, 0.8)

        cursor = await db.execute(
            "SELECT current_status FROM project_budgets WHERE project_id = ?",
            (project.id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["current_status"] == "warn"


# ---------------------------------------------------------------------------
# Full check_budgets integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCheckBudgets:
    async def test_no_budgets_no_op(self, db, event_bus, ws_hub) -> None:
        enforcer = BudgetEnforcer(db, event_bus, ws_hub=ws_hub)
        # Should not raise even with empty budgets table
        await enforcer._check_budgets()
        ws_hub.broadcast.assert_not_called()

    async def test_budget_transitions_on_check(self, db, event_bus, ws_hub) -> None:
        project = await create_project(db, "proj")
        await upsert_project_budget(db, project.id, monthly_cost_limit=10.0)
        # Record cost exceeding limit
        await write_usage_event(
            db,
            "ai_cost",
            project_id=project.id,
            cost_usd=15.0,
            input_tokens=1000,
            output_tokens=100,
        )

        enforcer = BudgetEnforcer(db, event_bus, ws_hub=ws_hub)
        await enforcer._check_budgets()

        cursor = await db.execute(
            "SELECT current_status FROM project_budgets WHERE project_id = ?",
            (project.id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["current_status"] == "exceeded"

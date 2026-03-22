"""Unit tests for section 15 open question resolutions.

Covers:
- Rolling 30-day budget window query (not calendar month)
- Tower _budget_constrained flag set on warning, cleared on ok
- Explicit cost reporting takes priority over stats-cache for same session
- atc tower cost CLI calls correct endpoint
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

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
from atc.tower.controller import BudgetConstrainedError, TowerController
from atc.tracking.budget import BudgetEnforcer
from atc.tracking.costs import CostTracker, calculate_cost


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


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
# 1. Rolling 30-day budget window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRolling30DayBudgetWindow:
    async def test_old_cost_outside_window_not_counted(self, db, event_bus) -> None:
        """Costs older than 30 days must not count toward the monthly limit."""
        project = await create_project(db, "proj-rolling")

        # Insert a cost event 31 days ago (outside rolling window)
        old_date = (datetime.now(UTC) - timedelta(days=31)).isoformat()
        await db.execute(
            """INSERT INTO usage_events
               (id, project_id, session_id, event_type, model,
                input_tokens, output_tokens, cost_usd, recorded_at)
               VALUES (?, ?, NULL, 'ai_cost', 'claude-sonnet-4-6', 0, 0, ?, ?)""",
            ("evt-old", project.id, 90.0, old_date),
        )
        await db.commit()

        enforcer = BudgetEnforcer(db, event_bus)
        # Limit is $100; old cost is $90 — should be OK because it's outside window
        status = await enforcer._compute_status(project.id, None, 100.0, 0.8)
        assert status == "ok", "Old spend outside 30-day window should not trigger warn"

    async def test_recent_cost_inside_window_counted(self, db, event_bus) -> None:
        """Costs within the last 30 days must count toward the monthly limit."""
        project = await create_project(db, "proj-recent")

        # Insert a cost event 5 days ago (inside rolling window)
        recent_date = (datetime.now(UTC) - timedelta(days=5)).isoformat()
        await db.execute(
            """INSERT INTO usage_events
               (id, project_id, session_id, event_type, model,
                input_tokens, output_tokens, cost_usd, recorded_at)
               VALUES (?, ?, NULL, 'ai_cost', 'claude-sonnet-4-6', 0, 0, ?, ?)""",
            ("evt-recent", project.id, 90.0, recent_date),
        )
        await db.commit()

        enforcer = BudgetEnforcer(db, event_bus)
        # Limit is $100; recent cost is $90 — should trigger warn (90% >= 80%)
        status = await enforcer._compute_status(project.id, None, 100.0, 0.8)
        assert status == "warn", "Recent spend inside 30-day window should trigger warn"

    async def test_31_days_old_not_counted(self, db, event_bus) -> None:
        """Cost 31 days old must fall outside the rolling 30-day window."""
        project = await create_project(db, "proj-boundary")

        boundary_date = (datetime.now(UTC) - timedelta(days=31)).isoformat()
        await db.execute(
            """INSERT INTO usage_events
               (id, project_id, session_id, event_type, model,
                input_tokens, output_tokens, cost_usd, recorded_at)
               VALUES (?, ?, NULL, 'ai_cost', 'claude-sonnet-4-6', 0, 0, ?, ?)""",
            ("evt-boundary", project.id, 90.0, boundary_date),
        )
        await db.commit()

        enforcer = BudgetEnforcer(db, event_bus)
        status = await enforcer._compute_status(project.id, None, 100.0, 0.8)
        assert status == "ok", "Spend 31 days ago must not count in rolling 30-day window"

    async def test_budget_ok_event_published_on_recovery(
        self, db, event_bus, ws_hub
    ) -> None:
        """budget_ok must be published when status transitions to ok."""
        project = await create_project(db, "proj-ok")
        await upsert_project_budget(db, project.id, monthly_cost_limit=100.0)

        received: list[dict] = []
        event_bus.subscribe("budget_ok", lambda d: received.append(d) or _noop())

        async def _collect(d: dict) -> None:
            received.append(d)

        event_bus.subscribe("budget_ok", _collect)

        enforcer = BudgetEnforcer(db, event_bus, ws_hub=ws_hub)
        await enforcer._transition_status(project.id, "warn", "ok", None, 100.0, 0.8)

        assert any(r.get("project_id") == project.id for r in received)


def _noop() -> None:
    pass


# ---------------------------------------------------------------------------
# 2. Tower budget_constrained flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTowerBudgetConstrainedFlag:
    async def test_flag_false_initially(self, db, event_bus) -> None:
        tower = TowerController(db, event_bus)
        assert tower._budget_constrained is False

    async def test_flag_set_on_budget_warning_event(self, db, event_bus) -> None:
        tower = TowerController(db, event_bus)
        await event_bus.publish("budget_warning", {"project_id": "proj-1"})
        assert tower._budget_constrained is True

    async def test_flag_cleared_on_budget_ok_event(self, db, event_bus) -> None:
        tower = TowerController(db, event_bus)
        # Set the flag first
        await event_bus.publish("budget_warning", {"project_id": "proj-1"})
        assert tower._budget_constrained is True
        # Then clear it
        await event_bus.publish("budget_ok", {"project_id": "proj-1"})
        assert tower._budget_constrained is False

    async def test_submit_goal_raises_when_budget_constrained(
        self, db, event_bus
    ) -> None:
        """submit_goal must refuse new work when budget constrained."""
        tower = TowerController(db, event_bus)
        await event_bus.publish("budget_warning", {"project_id": "proj-1"})

        with pytest.raises(BudgetConstrainedError):
            await tower.submit_goal("proj-1", "do something")


# ---------------------------------------------------------------------------
# 3. Explicit cost reporting takes priority over stats-cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExplicitCostReporting:
    async def test_record_explicit_writes_usage_event(self, db, event_bus) -> None:
        project = await create_project(db, "proj-explicit")
        session = await create_session(db, project.id, "ace", "ace-1")

        tracker = CostTracker(db, event_bus)
        await tracker.record_explicit(
            session.id, input_tokens=1000, output_tokens=200,
            model="claude-sonnet-4-6", cost_usd=0.006,
        )

        cursor = await db.execute(
            "SELECT * FROM usage_events WHERE session_id = ?", (session.id,)
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert float(rows[0]["cost_usd"]) == pytest.approx(0.006)

    async def test_record_explicit_marks_session_as_explicit(
        self, db, event_bus
    ) -> None:
        project = await create_project(db, "proj-mark")
        session = await create_session(db, project.id, "ace", "ace-1")

        tracker = CostTracker(db, event_bus)
        assert session.id not in tracker._has_explicit_reporting

        await tracker.record_explicit(
            session.id, input_tokens=100, output_tokens=50,
            model="claude-sonnet-4-6", cost_usd=0.001,
        )
        assert session.id in tracker._has_explicit_reporting

    async def test_poll_skips_explicit_session(self, db, event_bus, tmp_path) -> None:
        """Stats-cache poll must not write usage_events for explicit-reporting sessions."""
        project = await create_project(db, "proj-skip")
        session = await create_session(
            db, project.id, "ace", "ace-1", status="working"
        )

        # Fake stats-cache with a delta
        stats = tmp_path / "stats-cache.json"
        stats.write_text(json.dumps({
            "models": {"claude-sonnet-4-6": {"input_tokens": 500, "output_tokens": 100}}
        }))

        tracker = CostTracker(db, event_bus, stats_path=stats)

        # Mark session as explicit
        tracker._has_explicit_reporting.add(session.id)

        # Prime the previous snapshot so there's a delta
        tracker._last_snapshot = {
            "models": {"claude-sonnet-4-6": {"input_tokens": 0, "output_tokens": 0}}
        }

        await tracker._poll_once()

        cursor = await db.execute(
            "SELECT COUNT(*) FROM usage_events WHERE session_id = ?", (session.id,)
        )
        row = await cursor.fetchone()
        assert row[0] == 0, "Poll should not write events for explicit-reporting session"

    async def test_cost_reported_event_triggers_record_explicit(
        self, db, event_bus
    ) -> None:
        """cost_reported event on event bus should call record_explicit."""
        project = await create_project(db, "proj-event")
        session = await create_session(db, project.id, "ace", "ace-1")

        tracker = CostTracker(db, event_bus)
        await event_bus.publish(
            "cost_reported",
            {
                "session_id": session.id,
                "input_tokens": 200,
                "output_tokens": 80,
                "model": "claude-sonnet-4-6",
            },
        )

        assert session.id in tracker._has_explicit_reporting
        cursor = await db.execute(
            "SELECT COUNT(*) FROM usage_events WHERE session_id = ?", (session.id,)
        )
        row = await cursor.fetchone()
        assert row[0] == 1


# ---------------------------------------------------------------------------
# 4. atc tower cost CLI calls correct endpoint
# ---------------------------------------------------------------------------


class TestTowerCostCli:
    def test_cost_command_posts_to_correct_endpoint(self) -> None:
        """_handle_cost must POST to /api/tower/cost with all fields."""
        import argparse

        from atc.cli.tower import _handle_cost

        args = argparse.Namespace(
            api="http://127.0.0.1:8420",
            session_id="sess-abc",
            input_tokens=1000,
            output_tokens=250,
            model="claude-sonnet-4-6",
        )

        captured_url: list[str] = []
        captured_payload: list[dict] = []

        def fake_post_json(url: str, payload: dict) -> int:
            captured_url.append(url)
            captured_payload.append(payload)
            return 0

        with patch("atc.cli.tower._post_json", side_effect=fake_post_json):
            result = _handle_cost(args)

        assert result == 0
        assert captured_url[0] == "http://127.0.0.1:8420/api/tower/cost"
        assert captured_payload[0] == {
            "session_id": "sess-abc",
            "input_tokens": 1000,
            "output_tokens": 250,
            "model": "claude-sonnet-4-6",
        }

    def test_cost_command_registered_in_cli(self) -> None:
        """atc tower cost subcommand must be registered."""
        import argparse

        from atc.cli import tower as tower_cli

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()
        tower_cli.register(subparsers)

        # Parse a cost command to confirm it's registered
        args = parser.parse_args([
            "tower", "cost", "sess-123", "500", "100", "claude-sonnet-4-6",
        ])
        assert args.session_id == "sess-123"
        assert args.input_tokens == 500
        assert args.output_tokens == 100
        assert args.model == "claude-sonnet-4-6"

"""Unit tests for token-only usage tracking and budget constraints."""

from __future__ import annotations

import argparse
import json
from unittest.mock import patch

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
from atc.tracking.tokens import TokenTracker


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


def _noop() -> None:
    pass


@pytest.mark.asyncio
class TestTokenBudgetWindow:
    async def test_daily_token_limit_warns(self, db, event_bus) -> None:
        project = await create_project(db, "proj-token-warn")
        await write_usage_event(
            db,
            "ai_tokens",
            project_id=project.id,
            input_tokens=850,
            output_tokens=0,
        )

        enforcer = BudgetEnforcer(db, event_bus)
        status = await enforcer._compute_status(project.id, 1000, 0.8)
        assert status == "warn"

    async def test_daily_token_limit_exceeded(self, db, event_bus) -> None:
        project = await create_project(db, "proj-token-exceeded")
        await write_usage_event(
            db,
            "ai_tokens",
            project_id=project.id,
            input_tokens=1001,
            output_tokens=0,
        )

        enforcer = BudgetEnforcer(db, event_bus)
        status = await enforcer._compute_status(project.id, 1000, 0.8)
        assert status == "exceeded"

    async def test_budget_ok_event_published_on_recovery(self, db, event_bus) -> None:
        project = await create_project(db, "proj-ok")
        await upsert_project_budget(db, project.id, daily_token_limit=1000)

        received: list[dict] = []
        event_bus.subscribe("budget_ok", lambda d: received.append(d) or _noop())

        enforcer = BudgetEnforcer(db, event_bus)
        await enforcer._transition_status(project.id, "warn", "ok", 1000, 0.8)

        assert any(r.get("project_id") == project.id for r in received)


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
        await event_bus.publish("budget_warning", {"project_id": "proj-1"})
        await event_bus.publish("budget_ok", {"project_id": "proj-1"})
        assert tower._budget_constrained is False

    async def test_submit_goal_raises_when_budget_constrained(self, db, event_bus) -> None:
        tower = TowerController(db, event_bus)
        await event_bus.publish("budget_warning", {"project_id": "proj-1"})

        with pytest.raises(BudgetConstrainedError):
            await tower.submit_goal("proj-1", "do something")


@pytest.mark.asyncio
class TestExplicitTokenReporting:
    async def test_record_explicit_writes_usage_event(self, db, event_bus) -> None:
        project = await create_project(db, "proj-explicit")
        session = await create_session(db, project.id, "ace", "ace-1")

        tracker = TokenTracker(db, event_bus)
        await tracker.record_explicit(
            session.id,
            input_tokens=1000,
            output_tokens=200,
            model="claude-sonnet-4-6",
        )

        cursor = await db.execute(
            "SELECT * FROM usage_events WHERE session_id = ?", (session.id,)
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0]["event_type"] == "ai_tokens"
        assert rows[0]["input_tokens"] == 1000
        assert rows[0]["output_tokens"] == 200

    async def test_record_explicit_marks_session_as_explicit(self, db, event_bus) -> None:
        project = await create_project(db, "proj-mark")
        session = await create_session(db, project.id, "ace", "ace-1")

        tracker = TokenTracker(db, event_bus)
        assert session.id not in tracker._has_explicit_reporting

        await tracker.record_explicit(
            session.id,
            input_tokens=100,
            output_tokens=50,
            model="claude-sonnet-4-6",
        )
        assert session.id in tracker._has_explicit_reporting

    async def test_poll_skips_explicit_session(self, db, event_bus, tmp_path) -> None:
        project = await create_project(db, "proj-skip")
        session = await create_session(
            db, project.id, "ace", "ace-1", status="working"
        )

        stats = tmp_path / "stats-cache.json"
        stats.write_text(json.dumps({
            "models": {"claude-sonnet-4-6": {"input_tokens": 500, "output_tokens": 100}}
        }))

        tracker = TokenTracker(db, event_bus, stats_path=stats)
        tracker._has_explicit_reporting.add(session.id)
        tracker._last_snapshot = {
            "models": {"claude-sonnet-4-6": {"input_tokens": 0, "output_tokens": 0}}
        }

        await tracker._poll_once()

        cursor = await db.execute(
            "SELECT COUNT(*) FROM usage_events WHERE session_id = ?", (session.id,)
        )
        row = await cursor.fetchone()
        assert row[0] == 0

    async def test_tokens_reported_event_triggers_record_explicit(self, db, event_bus) -> None:
        project = await create_project(db, "proj-event")
        session = await create_session(db, project.id, "ace", "ace-1")

        tracker = TokenTracker(db, event_bus)
        await event_bus.publish(
            "tokens_reported",
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


class TestTowerTokensCli:
    def test_tokens_command_posts_to_correct_endpoint(self) -> None:
        """_handle_tokens must POST to /api/tower/tokens with all fields."""
        from atc.cli.tower import _handle_tokens

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
            result = _handle_tokens(args)

        assert result == 0
        assert captured_url[0] == "http://127.0.0.1:8420/api/tower/tokens"
        assert captured_payload[0] == {
            "session_id": "sess-abc",
            "input_tokens": 1000,
            "output_tokens": 250,
            "model": "claude-sonnet-4-6",
        }

    def test_tokens_command_registered_in_cli(self) -> None:
        from atc.cli import tower as tower_cli

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers()
        tower_cli.register(subparsers)

        args = parser.parse_args([
            "tower", "tokens", "sess-123", "500", "100", "claude-sonnet-4-6",
        ])
        assert args.session_id == "sess-123"
        assert args.input_tokens == 500
        assert args.output_tokens == 100
        assert args.model == "claude-sonnet-4-6"

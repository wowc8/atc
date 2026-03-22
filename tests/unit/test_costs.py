"""Unit tests for cost tracker — calculate_cost and delta computation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from atc.core.events import EventBus
from atc.state.db import (
    _SCHEMA_SQL,
    get_connection,
    run_migrations,
)
from atc.tracking.costs import CostTracker, calculate_cost

# ---------------------------------------------------------------------------
# calculate_cost
# ---------------------------------------------------------------------------


class TestCalculateCost:
    def test_sonnet_input_cost(self) -> None:
        cost = calculate_cost("claude-sonnet-4-6", 1_000_000, 0)
        assert abs(cost - 3.00) < 0.001

    def test_sonnet_output_cost(self) -> None:
        cost = calculate_cost("claude-sonnet-4-6", 0, 1_000_000)
        assert abs(cost - 15.00) < 0.001

    def test_opus_input_cost(self) -> None:
        cost = calculate_cost("claude-opus-4-6", 1_000_000, 0)
        assert abs(cost - 15.00) < 0.001

    def test_opus_output_cost(self) -> None:
        cost = calculate_cost("claude-opus-4-6", 0, 1_000_000)
        assert abs(cost - 75.00) < 0.001

    def test_haiku_input_cost(self) -> None:
        cost = calculate_cost("claude-haiku-4-5", 1_000_000, 0)
        assert abs(cost - 0.80) < 0.001

    def test_haiku_output_cost(self) -> None:
        cost = calculate_cost("claude-haiku-4-5", 0, 1_000_000)
        assert abs(cost - 4.00) < 0.001

    def test_unknown_model_falls_back_to_sonnet(self) -> None:
        cost_unknown = calculate_cost("unknown-model-xyz", 1_000_000, 0)
        cost_sonnet = calculate_cost("claude-sonnet-4-6", 1_000_000, 0)
        assert abs(cost_unknown - cost_sonnet) < 0.001

    def test_combined_input_output(self) -> None:
        cost = calculate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
        assert abs(cost - 18.00) < 0.001  # $3 in + $15 out

    def test_zero_tokens(self) -> None:
        assert calculate_cost("claude-sonnet-4-6", 0, 0) == 0.0

    def test_small_token_count(self) -> None:
        # 1000 input tokens of sonnet = $3 / 1000 = $0.003
        cost = calculate_cost("claude-sonnet-4-6", 1000, 0)
        assert abs(cost - 0.003) < 0.0001


# ---------------------------------------------------------------------------
# _compute_delta
# ---------------------------------------------------------------------------


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def ws_hub() -> MagicMock:
    hub = MagicMock()
    hub.broadcast = AsyncMock()
    return hub


class TestComputeDelta:
    def _make_tracker(self, event_bus: EventBus) -> CostTracker:
        db = MagicMock()
        return CostTracker(db, event_bus)

    def test_empty_snapshots_no_delta(self, event_bus: EventBus) -> None:
        tracker = self._make_tracker(event_bus)
        delta = tracker._compute_delta({}, {})
        assert delta == {}

    def test_first_snapshot_is_delta(self, event_bus: EventBus) -> None:
        tracker = self._make_tracker(event_bus)
        current = {
            "models": {
                "claude-sonnet-4-6": {"input_tokens": 1000, "output_tokens": 100}
            }
        }
        delta = tracker._compute_delta(current, {})
        assert "claude-sonnet-4-6" in delta
        assert delta["claude-sonnet-4-6"]["input_tokens"] == 1000
        assert delta["claude-sonnet-4-6"]["output_tokens"] == 100

    def test_incremental_delta(self, event_bus: EventBus) -> None:
        tracker = self._make_tracker(event_bus)
        prev = {
            "models": {
                "claude-sonnet-4-6": {"input_tokens": 1000, "output_tokens": 100}
            }
        }
        current = {
            "models": {
                "claude-sonnet-4-6": {"input_tokens": 1500, "output_tokens": 200}
            }
        }
        delta = tracker._compute_delta(current, prev)
        assert delta["claude-sonnet-4-6"]["input_tokens"] == 500
        assert delta["claude-sonnet-4-6"]["output_tokens"] == 100

    def test_no_change_no_delta(self, event_bus: EventBus) -> None:
        tracker = self._make_tracker(event_bus)
        snap = {
            "models": {
                "claude-sonnet-4-6": {"input_tokens": 1000, "output_tokens": 100}
            }
        }
        delta = tracker._compute_delta(snap, snap)
        assert delta == {}

    def test_negative_delta_ignored(self, event_bus: EventBus) -> None:
        """Counter resets (e.g. new session file) should not produce negative deltas."""
        tracker = self._make_tracker(event_bus)
        prev = {
            "models": {
                "claude-sonnet-4-6": {"input_tokens": 5000, "output_tokens": 500}
            }
        }
        current = {
            "models": {
                "claude-sonnet-4-6": {"input_tokens": 100, "output_tokens": 10}
            }
        }
        delta = tracker._compute_delta(current, prev)
        assert delta == {}

    def test_multi_model_delta(self, event_bus: EventBus) -> None:
        tracker = self._make_tracker(event_bus)
        prev = {
            "models": {
                "claude-sonnet-4-6": {"input_tokens": 1000, "output_tokens": 100},
                "claude-opus-4-6": {"input_tokens": 500, "output_tokens": 50},
            }
        }
        current = {
            "models": {
                "claude-sonnet-4-6": {"input_tokens": 2000, "output_tokens": 200},
                "claude-opus-4-6": {"input_tokens": 600, "output_tokens": 60},
            }
        }
        delta = tracker._compute_delta(current, prev)
        assert delta["claude-sonnet-4-6"]["input_tokens"] == 1000
        assert delta["claude-opus-4-6"]["input_tokens"] == 100

    def test_flat_format_extraction(self, event_bus: EventBus) -> None:
        tracker = self._make_tracker(event_bus)
        data = {"model": "claude-sonnet-4-6", "input_tokens": 500, "output_tokens": 50}
        result = tracker._extract_model_data(data)
        assert "claude-sonnet-4-6" in result
        assert result["claude-sonnet-4-6"]["input_tokens"] == 500


# ---------------------------------------------------------------------------
# DB write integration
# ---------------------------------------------------------------------------


@pytest.fixture
async def db():
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.mark.asyncio
class TestCostTrackerPoll:
    async def test_poll_writes_usage_event(
        self, db, event_bus: EventBus, ws_hub: MagicMock, tmp_path
    ) -> None:
        stats_file = tmp_path / "stats-cache.json"
        stats_file.write_text(
            '{"models": {"claude-sonnet-4-6": {"input_tokens": 1000, "output_tokens": 100}}}'
        )

        tracker = CostTracker(db, event_bus, ws_hub=ws_hub, stats_path=stats_file)
        # First poll loads initial snapshot
        await tracker._poll_once()

        cursor = await db.execute("SELECT * FROM usage_events WHERE event_type = 'ai_cost'")
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0]["model"] == "claude-sonnet-4-6"
        assert rows[0]["input_tokens"] == 1000
        assert rows[0]["output_tokens"] == 100

    async def test_poll_computes_incremental_delta(
        self, db, event_bus: EventBus, ws_hub: MagicMock, tmp_path
    ) -> None:
        stats_file = tmp_path / "stats-cache.json"
        stats_file.write_text(
            '{"models": {"claude-sonnet-4-6": {"input_tokens": 1000, "output_tokens": 100}}}'
        )

        tracker = CostTracker(db, event_bus, ws_hub=ws_hub, stats_path=stats_file)
        await tracker._poll_once()  # snapshot = 1000/100

        # Simulate new tokens
        stats_file.write_text(
            '{"models": {"claude-sonnet-4-6": {"input_tokens": 1500, "output_tokens": 200}}}'
        )
        await tracker._poll_once()  # delta = 500/100

        cursor = await db.execute(
            "SELECT input_tokens FROM usage_events"
            " WHERE event_type = 'ai_cost' ORDER BY recorded_at"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 2
        assert rows[1]["input_tokens"] == 500

    async def test_poll_missing_file_no_op(
        self, db, event_bus: EventBus, ws_hub: MagicMock, tmp_path
    ) -> None:
        stats_file = tmp_path / "nonexistent-stats.json"
        tracker = CostTracker(db, event_bus, ws_hub=ws_hub, stats_path=stats_file)
        await tracker._poll_once()

        cursor = await db.execute("SELECT COUNT(*) FROM usage_events")
        row = await cursor.fetchone()
        assert row[0] == 0

    async def test_poll_broadcasts_to_ws(
        self, db, event_bus: EventBus, ws_hub: MagicMock, tmp_path
    ) -> None:
        stats_file = tmp_path / "stats-cache.json"
        stats_file.write_text(
            '{"models": {"claude-sonnet-4-6": {"input_tokens": 100, "output_tokens": 10}}}'
        )

        tracker = CostTracker(db, event_bus, ws_hub=ws_hub, stats_path=stats_file)
        await tracker._poll_once()

        ws_hub.broadcast.assert_called_once()
        channel = ws_hub.broadcast.call_args[0][0]
        assert channel == "costs"

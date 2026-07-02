"""Unit tests for provider-neutral token usage recording."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from atc.core.events import EventBus
from atc.state.db import (
    get_connection,
    get_usage_source_offset,
    run_migrations,
    upsert_usage_source_offset,
)
from atc.tracking.tokens import TokenUsageIncrement, TokenUsageRecorder


@pytest.fixture
async def migrated_db(tmp_path: Path):
    db_path = tmp_path / "atc.db"
    await run_migrations(str(db_path))
    async with get_connection(str(db_path)) as conn:
        yield conn


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def ws_hub() -> MagicMock:
    hub = MagicMock()
    hub.broadcast = AsyncMock()
    return hub


def increment(**overrides: object) -> TokenUsageIncrement:
    values = {
        "session_id": "session-1",
        "project_id": "project-1",
        "provider": "fake_provider",
        "model": "fake-model",
        "recorded_at": datetime(2026, 7, 2, 12, 0, tzinfo=UTC),
        "input_tokens": 10,
        "cached_input_tokens": 3,
        "output_tokens": 5,
        "reasoning_output_tokens": 2,
        "total_tokens": 17,
        "source": "unit_test",
        "external_session_id": "external-1",
        "source_event_id": "event-1",
        "source_file": "/tmp/provider.jsonl",
        "source_offset": 123,
        "raw_usage": {"raw": {"total_tokens": 17}},
    }
    values.update(overrides)
    return TokenUsageIncrement(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
class TestTokenUsageRecorder:
    async def test_records_provider_neutral_increment(
        self, migrated_db, event_bus: EventBus, ws_hub: MagicMock
    ) -> None:
        recorder = TokenUsageRecorder(migrated_db, event_bus, ws_hub=ws_hub)

        event_id = await recorder.record_increment(increment())

        assert event_id is not None
        cursor = await migrated_db.execute("SELECT * FROM usage_events WHERE id = ?", (event_id,))
        row = await cursor.fetchone()
        assert row is not None
        assert row["event_type"] == "ai_tokens"
        assert row["provider"] == "fake_provider"
        assert row["source"] == "unit_test"
        assert row["model"] == "fake-model"
        assert row["input_tokens"] == 10
        assert row["cached_input_tokens"] == 3
        assert row["output_tokens"] == 5
        assert row["reasoning_output_tokens"] == 2
        assert row["total_tokens"] == 17
        assert row["external_session_id"] == "external-1"
        assert row["source_event_id"] == "event-1"
        assert row["source_file"] == "/tmp/provider.jsonl"
        assert row["source_offset"] == 123
        assert json.loads(row["raw_usage_json"]) == {"raw": {"total_tokens": 17}}
        ws_hub.broadcast.assert_awaited_once()

    async def test_multiple_increments_aggregate_with_total_tokens(
        self, migrated_db, event_bus: EventBus
    ) -> None:
        recorder = TokenUsageRecorder(migrated_db, event_bus)
        await recorder.record_increment(increment(total_tokens=17, source_event_id="a"))
        await recorder.record_increment(increment(total_tokens=8, source_event_id="b"))

        cursor = await migrated_db.execute(
            """SELECT COALESCE(
                   SUM(COALESCE(
                       total_tokens,
                       COALESCE(input_tokens, 0)
                       + COALESCE(output_tokens, 0)
                       + COALESCE(reasoning_output_tokens, 0)
                   )),
                   0
               )
               FROM usage_events WHERE event_type = 'ai_tokens'"""
        )
        row = await cursor.fetchone()
        assert row[0] == 25

    async def test_zero_token_increment_is_ignored(
        self, migrated_db, event_bus: EventBus
    ) -> None:
        recorder = TokenUsageRecorder(migrated_db, event_bus)
        event_id = await recorder.record_increment(
            increment(
                input_tokens=0,
                cached_input_tokens=0,
                output_tokens=0,
                reasoning_output_tokens=0,
                total_tokens=0,
            )
        )

        assert event_id is None
        cursor = await migrated_db.execute("SELECT COUNT(*) FROM usage_events")
        row = await cursor.fetchone()
        assert row[0] == 0

    async def test_negative_increment_is_rejected(
        self, migrated_db, event_bus: EventBus
    ) -> None:
        recorder = TokenUsageRecorder(migrated_db, event_bus)

        with pytest.raises(ValueError, match="cannot be negative"):
            await recorder.record_increment(increment(input_tokens=-1))

    async def test_raw_usage_json_round_trips(
        self, migrated_db, event_bus: EventBus
    ) -> None:
        recorder = TokenUsageRecorder(migrated_db, event_bus)
        raw = {"provider": "fake", "nested": {"cached_input_tokens": 44}}
        event_id = await recorder.record_increment(increment(raw_usage=raw))

        cursor = await migrated_db.execute(
            "SELECT raw_usage_json FROM usage_events WHERE id = ?", (event_id,)
        )
        row = await cursor.fetchone()
        assert json.loads(row[0]) == raw


@pytest.mark.asyncio
class TestUsageSourceOffsets:
    async def test_high_water_offset_insert_update_read(self, migrated_db) -> None:
        first = await upsert_usage_source_offset(
            migrated_db,
            provider="fake_provider",
            source_key="source-a",
            external_session_id="external-a",
            byte_offset=10,
            last_input_tokens=100,
            last_cached_input_tokens=50,
            last_output_tokens=20,
            last_reasoning_output_tokens=5,
            last_total_tokens=125,
        )
        assert first.byte_offset == 10

        second = await upsert_usage_source_offset(
            migrated_db,
            provider="fake_provider",
            source_key="source-a",
            external_session_id="external-a",
            byte_offset=99,
            last_input_tokens=150,
            last_cached_input_tokens=80,
            last_output_tokens=30,
            last_reasoning_output_tokens=7,
            last_total_tokens=187,
        )
        assert second.byte_offset == 99

        loaded = await get_usage_source_offset(
            migrated_db, provider="fake_provider", source_key="source-a"
        )
        assert loaded is not None
        assert loaded.external_session_id == "external-a"
        assert loaded.byte_offset == 99
        assert loaded.last_input_tokens == 150
        assert loaded.last_cached_input_tokens == 80
        assert loaded.last_output_tokens == 30
        assert loaded.last_reasoning_output_tokens == 7
        assert loaded.last_total_tokens == 187
        assert loaded.created_at
        assert loaded.updated_at


@pytest.mark.asyncio
async def test_existing_usage_events_table_upgrades_without_dropping_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "existing.db"
    import aiosqlite

    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            """CREATE TABLE usage_events (
                   id TEXT PRIMARY KEY,
                   project_id TEXT,
                   session_id TEXT,
                   event_type TEXT NOT NULL,
                   model TEXT,
                   input_tokens INTEGER,
                   output_tokens INTEGER,
                   recorded_at TEXT NOT NULL
               )"""
        )
        await db.execute(
            """INSERT INTO usage_events
               (id, project_id, session_id, event_type, model,
                input_tokens, output_tokens, recorded_at)
               VALUES (
                   'old-event', 'project-1', 'session-1', 'ai_tokens',
                   'legacy', 4, 5, '2026-07-02T00:00:00+00:00'
               )"""
        )
        await db.commit()

    await run_migrations(str(db_path))

    async with get_connection(str(db_path)) as db:
        cursor = await db.execute(
            "SELECT input_tokens, output_tokens FROM usage_events WHERE id = 'old-event'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["input_tokens"] == 4
        assert row["output_tokens"] == 5

        cursor = await db.execute("PRAGMA table_info(usage_events)")
        columns = {str(row["name"]) for row in await cursor.fetchall()}
        assert "total_tokens" in columns
        assert "raw_usage_json" in columns


@pytest.mark.asyncio
async def test_migration_adds_provider_neutral_usage_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    await run_migrations(str(db_path))
    async with get_connection(str(db_path)) as db:
        cursor = await db.execute("PRAGMA table_info(usage_events)")
        columns = {str(row["name"]) for row in await cursor.fetchall()}
        assert {
            "provider",
            "source",
            "cached_input_tokens",
            "reasoning_output_tokens",
            "total_tokens",
            "external_session_id",
            "source_event_id",
            "source_file",
            "source_offset",
            "raw_usage_json",
        }.issubset(columns)

        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='usage_source_offsets'"
        )
        assert await cursor.fetchone() is not None


def test_shared_token_module_has_no_codex_specific_parsing() -> None:
    shared = Path("src/atc/tracking/tokens.py").read_text()
    forbidden = [".codex", "token_count", "rollout-", "codex_jsonl"]
    assert [term for term in forbidden if term in shared] == []


def test_no_cost_semantics_reintroduced_to_usage_foundation() -> None:
    paths = [
        Path("src/atc/tracking/tokens.py"),
        Path("src/atc/api/routers/usage.py"),
        Path("src/atc/state/models.py"),
        Path("src/atc/state/migrations/versions/019_provider_neutral_token_usage.sql"),
    ]
    forbidden = [
        "cost" + "_usd",
        "/api/tower/" + "cost",
        "/api/usage/" + "cost",
        "Cost" + "Tracker",
    ]
    offenders: list[tuple[str, str]] = []
    for path in paths:
        text = path.read_text()
        offenders.extend((str(path), term) for term in forbidden if term in text)
    assert offenders == []

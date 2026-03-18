"""Unit tests for structured app event logging."""

from __future__ import annotations

import pytest

from atc.core.app_events import count_events, list_events, log_event
from atc.state.db import _SCHEMA_SQL, get_connection, run_migrations


@pytest.fixture
async def db():
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.mark.asyncio
class TestAppEvents:
    async def test_log_and_list(self, db) -> None:
        event_id = await log_event(
            db,
            level="info",
            category="session",
            message="Session started",
            detail={"session_id": "s1"},
            project_id="p1",
        )
        assert event_id
        events = await list_events(db)
        assert len(events) == 1
        assert events[0]["id"] == event_id
        assert events[0]["level"] == "info"
        assert events[0]["category"] == "session"
        assert events[0]["detail"] == {"session_id": "s1"}

    async def test_list_empty(self, db) -> None:
        assert await list_events(db) == []

    async def test_filter_by_level(self, db) -> None:
        await log_event(db, level="info", category="session", message="m1")
        await log_event(db, level="error", category="error", message="m2")
        info = await list_events(db, level="info")
        assert len(info) == 1
        assert info[0]["level"] == "info"

    async def test_filter_by_category(self, db) -> None:
        await log_event(db, level="info", category="session", message="m1")
        await log_event(db, level="info", category="task", message="m2")
        tasks = await list_events(db, category="task")
        assert len(tasks) == 1

    async def test_filter_by_project(self, db) -> None:
        await log_event(db, level="info", category="session", message="m1", project_id="p1")
        await log_event(db, level="info", category="session", message="m2", project_id="p2")
        results = await list_events(db, project_id="p1")
        assert len(results) == 1

    async def test_count(self, db) -> None:
        for i in range(5):
            await log_event(db, level="info", category="session", message=f"m{i}")
        assert await count_events(db) == 5

    async def test_limit_and_offset(self, db) -> None:
        for i in range(10):
            await log_event(db, level="info", category="session", message=f"m{i}")
        page1 = await list_events(db, limit=3, offset=0)
        page2 = await list_events(db, limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3
        assert page1[0]["id"] != page2[0]["id"]

    async def test_ordering_newest_first(self, db) -> None:
        await log_event(db, level="info", category="session", message="first")
        await log_event(db, level="info", category="session", message="second")
        events = await list_events(db)
        assert events[0]["message"] == "second"

    async def test_detail_deserialized(self, db) -> None:
        await log_event(
            db,
            level="info",
            category="system",
            message="m1",
            detail={"key": "value", "nested": {"a": 1}},
        )
        results = await list_events(db)
        assert results[0]["detail"] == {"key": "value", "nested": {"a": 1}}

    async def test_null_detail(self, db) -> None:
        await log_event(db, level="info", category="system", message="m1")
        results = await list_events(db)
        assert results[0]["detail"] is None

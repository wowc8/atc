"""Unit tests for failure logs REST API router."""

from __future__ import annotations

import pytest

from atc.core.failure_log import list_failures, log_failure, resolve_failure
from atc.state.db import _SCHEMA_SQL, get_connection, run_migrations


@pytest.fixture
async def db():
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.mark.asyncio
class TestFailureLogsAPI:
    async def test_list_empty(self, db) -> None:
        failures = await list_failures(db)
        assert failures == []

    async def test_list_with_entries(self, db) -> None:
        await log_failure(
            db,
            level="error",
            category="creation_failure",
            message="pane spawn failed",
            context={"session_id": "s1"},
            project_id="p1",
            entity_type="ace",
            entity_id="s1",
        )
        await log_failure(
            db,
            level="warning",
            category="session_stalled",
            message="no progress in 5m",
            project_id="p1",
        )

        failures = await list_failures(db)
        assert len(failures) == 2
        assert failures[0]["level"] == "warning"  # newest first
        assert failures[1]["level"] == "error"

    async def test_filter_by_project(self, db) -> None:
        await log_failure(db, level="error", category="c1", message="m1", project_id="p1")
        await log_failure(db, level="error", category="c2", message="m2", project_id="p2")

        results = await list_failures(db, project_id="p1")
        assert len(results) == 1
        assert results[0]["project_id"] == "p1"

    async def test_resolve_and_filter(self, db) -> None:
        log_id = await log_failure(db, level="error", category="c1", message="m1")
        await resolve_failure(db, log_id)

        unresolved = await list_failures(db, resolved=False)
        assert len(unresolved) == 0

        resolved = await list_failures(db, resolved=True)
        assert len(resolved) == 1
        assert resolved[0]["resolved"] is True

    async def test_unresolved_count(self, db) -> None:
        await log_failure(db, level="error", category="c1", message="m1")
        await log_failure(db, level="warning", category="c2", message="m2")
        log_id = await log_failure(db, level="info", category="c3", message="m3")
        await resolve_failure(db, log_id)

        unresolved = await list_failures(db, resolved=False)
        assert len(unresolved) == 2

    async def test_limit(self, db) -> None:
        for i in range(5):
            await log_failure(db, level="error", category="c", message=f"m{i}")

        results = await list_failures(db, limit=3)
        assert len(results) == 3

    async def test_context_deserialized(self, db) -> None:
        await log_failure(
            db,
            level="error",
            category="c1",
            message="m1",
            context={"key": "value", "nested": {"a": 1}},
        )

        results = await list_failures(db)
        assert results[0]["context"] == {"key": "value", "nested": {"a": 1}}

"""Unit tests for failure logging."""

from __future__ import annotations

import pytest

from atc.core.failure_log import list_failures, log_failure, resolve_failure
from atc.state.db import get_connection, run_migrations, _SCHEMA_SQL


@pytest.fixture
async def db():
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.mark.asyncio
class TestFailureLog:
    async def test_log_and_list(self, db) -> None:
        log_id = await log_failure(
            db,
            level="error",
            category="creation_failure",
            message="tmux pane failed to spawn",
            context={"session_id": "s1"},
        )
        assert log_id

        failures = await list_failures(db)
        assert len(failures) == 1
        assert failures[0]["id"] == log_id
        assert failures[0]["level"] == "error"
        assert failures[0]["message"] == "tmux pane failed to spawn"
        assert failures[0]["context"] == {"session_id": "s1"}

    async def test_resolve(self, db) -> None:
        log_id = await log_failure(
            db, level="warning", category="session_stalled", message="no progress"
        )
        await resolve_failure(db, log_id)

        failures = await list_failures(db, resolved=True)
        assert len(failures) == 1
        assert failures[0]["resolved"] is True

    async def test_filter_by_level(self, db) -> None:
        await log_failure(db, level="error", category="c1", message="e1")
        await log_failure(db, level="warning", category="c2", message="w1")

        errors = await list_failures(db, level="error")
        assert len(errors) == 1
        assert errors[0]["level"] == "error"

    async def test_with_exception(self, db) -> None:
        try:
            raise ValueError("test error")
        except ValueError as e:
            log_id = await log_failure(
                db,
                level="error",
                category="unexpected",
                message="caught an error",
                exc=e,
            )

        failures = await list_failures(db)
        assert len(failures) == 1
        assert failures[0]["stack_trace"] is not None
        assert "ValueError" in failures[0]["stack_trace"]

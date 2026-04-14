"""Unit tests for the ATC memory subsystem.

Covers:
  - AceSTM write / get / clear / prune
  - ProjectLog append / get_recent
  - LongTermMemory write / FTS search / list
  - MemoryConsolidation.should_run() logic
  - MemoryCron skip logic
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atc.memory.ace_stm import AceSTM
from atc.memory.consolidation import MemoryConsolidation
from atc.memory.ltm import LongTermMemory
from atc.memory.project_log import ProjectLog
from atc.state.db import _SCHEMA_SQL, get_connection, run_migrations


# ---------------------------------------------------------------------------
# Test fixture: in-memory DB with full schema
# ---------------------------------------------------------------------------


@pytest.fixture
async def db():
    """Yield an aiosqlite connection backed by a fresh in-memory database."""
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


# ---------------------------------------------------------------------------
# AceSTM tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAceSTM:
    async def test_write_and_get(self, db) -> None:
        await AceSTM.write_progress(db, "sess-1", "Working on auth module", 5)
        content = await AceSTM.get_progress(db, "sess-1")
        assert content == "Working on auth module"

    async def test_upsert_updates_existing(self, db) -> None:
        await AceSTM.write_progress(db, "sess-1", "First snapshot", 5)
        await AceSTM.write_progress(db, "sess-1", "Second snapshot", 10)
        content = await AceSTM.get_progress(db, "sess-1")
        assert content == "Second snapshot"

        # Only one row should exist
        cursor = await db.execute("SELECT COUNT(*) AS cnt FROM ace_stm WHERE session_id = ?", ("sess-1",))
        row = await cursor.fetchone()
        assert row["cnt"] == 1

    async def test_get_missing_returns_none(self, db) -> None:
        result = await AceSTM.get_progress(db, "nonexistent")
        assert result is None

    async def test_clear(self, db) -> None:
        await AceSTM.write_progress(db, "sess-1", "progress", 3)
        assert await AceSTM.get_progress(db, "sess-1") is not None
        await AceSTM.clear(db, "sess-1")
        assert await AceSTM.get_progress(db, "sess-1") is None

    async def test_multiple_sessions(self, db) -> None:
        await AceSTM.write_progress(db, "sess-a", "work A", 1)
        await AceSTM.write_progress(db, "sess-b", "work B", 2)
        assert await AceSTM.get_progress(db, "sess-a") == "work A"
        assert await AceSTM.get_progress(db, "sess-b") == "work B"

    async def test_list_all(self, db) -> None:
        await AceSTM.write_progress(db, "sess-1", "snap1", 5)
        await AceSTM.write_progress(db, "sess-2", "snap2", 10)
        entries = await AceSTM.list_all(db)
        assert len(entries) == 2
        session_ids = {e["session_id"] for e in entries}
        assert session_ids == {"sess-1", "sess-2"}

    async def test_prune_old(self, db) -> None:
        # Insert one recent and one old entry directly
        old_ts = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
        now_ts = datetime.now(UTC).isoformat()
        import uuid

        await db.execute(
            "INSERT INTO ace_stm (id, session_id, content, tool_call_count, created_at, updated_at)"
            " VALUES (?, 'old-sess', 'old content', 3, ?, ?)",
            (str(uuid.uuid4()), old_ts, old_ts),
        )
        await db.execute(
            "INSERT INTO ace_stm (id, session_id, content, tool_call_count, created_at, updated_at)"
            " VALUES (?, 'new-sess', 'new content', 5, ?, ?)",
            (str(uuid.uuid4()), now_ts, now_ts),
        )
        await db.commit()

        deleted = await AceSTM.prune_old(db, max_age_hours=24)
        assert deleted == 1
        assert await AceSTM.get_progress(db, "old-sess") is None
        assert await AceSTM.get_progress(db, "new-sess") == "new content"


# ---------------------------------------------------------------------------
# ProjectLog tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestProjectLog:
    async def _create_project(self, db) -> str:
        """Helper: insert a minimal project row and return its id."""
        import uuid

        pid = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        await db.execute(
            "INSERT INTO projects (id, name, status, agent_provider, created_at, updated_at)"
            " VALUES (?, 'Test Project', 'active', 'claude_code', ?, ?)",
            (pid, now, now),
        )
        await db.commit()
        return pid

    async def test_append_creates_entry(self, db) -> None:
        pid = await self._create_project(db)
        await ProjectLog.append(db, pid, "sess-1", "finding", "Found a bug", "sess-1")
        entries = await ProjectLog.get_recent(db, pid)
        assert len(entries) == 1
        assert entries[0]["content"] == "Found a bug"
        assert entries[0]["type"] == "finding"
        assert entries[0]["by"] == "sess-1"

    async def test_append_multiple(self, db) -> None:
        pid = await self._create_project(db)
        await ProjectLog.append(db, pid, "sess-1", "decision", "Use JWT", "tower")
        await ProjectLog.append(db, pid, "sess-2", "result", "Done!", "sess-2")
        await ProjectLog.append(db, pid, "sess-1", "error", "DB timeout", "sess-1")

        entries = await ProjectLog.get_recent(db, pid)
        assert len(entries) == 3
        assert entries[-1]["type"] == "error"  # last entry

    async def test_get_recent_limit(self, db) -> None:
        pid = await self._create_project(db)
        for i in range(10):
            await ProjectLog.append(db, pid, "sess-1", "finding", f"finding {i}", "sess-1")

        recent = await ProjectLog.get_recent(db, pid, limit=3)
        assert len(recent) == 3
        # Should be the last 3 (indices 7, 8, 9)
        assert recent[-1]["content"] == "finding 9"

    async def test_get_recent_empty(self, db) -> None:
        pid = await self._create_project(db)
        entries = await ProjectLog.get_recent(db, pid)
        assert entries == []

    async def test_invalid_type_coerced(self, db) -> None:
        pid = await self._create_project(db)
        await ProjectLog.append(db, pid, "sess-1", "bogus_type", "content", "sess-1")
        entries = await ProjectLog.get_recent(db, pid)
        assert entries[0]["type"] == "finding"  # coerced

    async def test_ts_format(self, db) -> None:
        pid = await self._create_project(db)
        await ProjectLog.append(db, pid, "sess-1", "result", "OK", "sess-1")
        entries = await ProjectLog.get_recent(db, pid)
        ts = entries[0]["ts"]
        # Should parse as ISO-8601 without raising
        datetime.fromisoformat(ts)


# ---------------------------------------------------------------------------
# LongTermMemory tests (FTS — no API needed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestLongTermMemory:
    async def test_write_and_get(self, db) -> None:
        entry_id = await LongTermMemory.write(db, "auth-pattern", "Always validate JWTs server-side")
        assert entry_id

        rec = await LongTermMemory.get(db, "auth-pattern")
        assert rec is not None
        assert rec.key == "auth-pattern"
        assert rec.value == "Always validate JWTs server-side"
        assert rec.project_id is None

    async def test_write_upsert(self, db) -> None:
        id1 = await LongTermMemory.write(db, "key1", "first value")
        id2 = await LongTermMemory.write(db, "key1", "updated value")
        # Same key → same id
        assert id1 == id2
        rec = await LongTermMemory.get(db, "key1")
        assert rec is not None
        assert rec.value == "updated value"

    async def test_write_with_project_id(self, db) -> None:
        await LongTermMemory.write(db, "proj-key", "project-scoped value", project_id="proj-123")
        rec = await LongTermMemory.get(db, "proj-key")
        assert rec is not None
        assert rec.project_id == "proj-123"

    async def test_fts_search(self, db) -> None:
        await LongTermMemory.write(db, "jwt-tip", "JWT tokens should be signed with RS256")
        await LongTermMemory.write(db, "db-tip", "Use WAL mode for better SQLite concurrency")

        results = await LongTermMemory.search_fts(db, "JWT")
        assert len(results) >= 1
        assert any(r.key == "jwt-tip" for r in results)

    async def test_fts_search_no_match(self, db) -> None:
        await LongTermMemory.write(db, "tip", "Some unrelated content")
        results = await LongTermMemory.search_fts(db, "xyznonexistentterm")
        assert results == []

    async def test_fts_search_multiple_results(self, db) -> None:
        await LongTermMemory.write(db, "tip-1", "Python async is great for I/O")
        await LongTermMemory.write(db, "tip-2", "Python type hints improve reliability")
        await LongTermMemory.write(db, "tip-3", "Go is fast but Python is expressive")

        results = await LongTermMemory.search_fts(db, "Python")
        assert len(results) >= 2

    async def test_list_all(self, db) -> None:
        await LongTermMemory.write(db, "k1", "v1")
        await LongTermMemory.write(db, "k2", "v2")
        all_entries = await LongTermMemory.list_all(db)
        assert len(all_entries) == 2

    async def test_list_all_pagination(self, db) -> None:
        for i in range(5):
            await LongTermMemory.write(db, f"key-{i}", f"value-{i}")
        page1 = await LongTermMemory.list_all(db, limit=3, offset=0)
        page2 = await LongTermMemory.list_all(db, limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 2

    async def test_delete(self, db) -> None:
        await LongTermMemory.write(db, "to-delete", "temp value")
        assert await LongTermMemory.get(db, "to-delete") is not None
        deleted = await LongTermMemory.delete(db, "to-delete")
        assert deleted is True
        assert await LongTermMemory.get(db, "to-delete") is None

    async def test_delete_nonexistent(self, db) -> None:
        deleted = await LongTermMemory.delete(db, "ghost-key")
        assert deleted is False

    async def test_semantic_search_falls_back_to_fts(self, db) -> None:
        """With no embeddings, semantic search should fall back to FTS5."""
        await LongTermMemory.write(db, "fallback-tip", "Retry on transient failures")
        # No embeddings stored (embedding generation returns None by default)
        results = await LongTermMemory.search_semantic(db, "retry transient")
        # Falls back to FTS — may or may not match, but should not raise
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# MemoryConsolidation.should_run() logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestConsolidationShouldRun:
    async def test_returns_true_with_no_runs(self, db) -> None:
        assert await MemoryConsolidation.should_run(db) is True

    async def test_returns_false_when_run_recently(self, db) -> None:
        import uuid

        now = datetime.now(UTC).isoformat()
        await db.execute(
            """INSERT INTO memory_consolidation_runs
               (id, started_at, finished_at, entries_processed, entries_written, status)
               VALUES (?, ?, ?, 0, 0, 'done')""",
            (str(uuid.uuid4()), now, now),
        )
        await db.commit()
        assert await MemoryConsolidation.should_run(db) is False

    async def test_returns_true_when_last_run_was_old(self, db) -> None:
        import uuid

        old = (datetime.now(UTC) - timedelta(hours=4)).isoformat()
        await db.execute(
            """INSERT INTO memory_consolidation_runs
               (id, started_at, finished_at, entries_processed, entries_written, status)
               VALUES (?, ?, ?, 0, 0, 'done')""",
            (str(uuid.uuid4()), old, old),
        )
        await db.commit()
        assert await MemoryConsolidation.should_run(db) is True

    async def test_running_status_ignored(self, db) -> None:
        """A 'running' row (no finished_at) should not prevent re-check."""
        import uuid

        now = datetime.now(UTC).isoformat()
        await db.execute(
            """INSERT INTO memory_consolidation_runs
               (id, started_at, entries_processed, entries_written, status)
               VALUES (?, ?, 0, 0, 'running')""",
            (str(uuid.uuid4()), now),
        )
        await db.commit()
        # No finished 'done' row → should run
        assert await MemoryConsolidation.should_run(db) is True

    async def test_should_run_for_day_true_when_no_runs_today(self, db) -> None:
        assert await MemoryConsolidation.should_run_for_day(db) is True

    async def test_should_run_for_day_false_when_ran_today(self, db) -> None:
        import uuid

        now = datetime.now(UTC).isoformat()
        await db.execute(
            """INSERT INTO memory_consolidation_runs
               (id, started_at, finished_at, entries_processed, entries_written, status)
               VALUES (?, ?, ?, 0, 0, 'done')""",
            (str(uuid.uuid4()), now, now),
        )
        await db.commit()
        assert await MemoryConsolidation.should_run_for_day(db) is False


# ---------------------------------------------------------------------------
# MemoryCron skip logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMemoryCron:
    async def test_trigger_now_dispatches_job(self, db) -> None:
        from atc.memory.cron import MemoryCron

        mock_bus = MagicMock()
        cron = MemoryCron(":memory:", mock_bus)

        with patch(
            "atc.memory.cron.MemoryConsolidation.run_consolidation",
            new_callable=AsyncMock,
        ) as mock_consolidate:
            from atc.memory.consolidation import ConsolidationResult

            mock_consolidate.return_value = ConsolidationResult(
                run_id="r1", entries_processed=0, entries_written=0, status="done"
            )
            await cron.trigger_now()
            # Wait for the background task
            import asyncio

            await asyncio.sleep(0.05)
            mock_consolidate.assert_called_once()

    async def test_skip_when_job_already_running(self, db) -> None:
        from atc.memory.cron import MemoryCron

        mock_bus = MagicMock()
        cron = MemoryCron(":memory:", mock_bus)

        async def _slow_job(*args, **kwargs):  # type: ignore[no-untyped-def]
            import asyncio

            await asyncio.sleep(10)

        with patch(
            "atc.memory.cron.MemoryConsolidation.run_consolidation",
            side_effect=_slow_job,
        ):
            await cron.trigger_now()
            import asyncio

            await asyncio.sleep(0.01)
            assert cron.is_running_job()
            # Triggering again should not dispatch a second job
            with patch(
                "atc.memory.cron.MemoryConsolidation.run_consolidation",
                new_callable=AsyncMock,
            ) as mock2:
                await cron.trigger_now()
                await asyncio.sleep(0.01)
                mock2.assert_not_called()

            # Clean up
            if cron._running_job and not cron._running_job.done():
                cron._running_job.cancel()

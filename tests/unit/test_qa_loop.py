"""Unit tests for the QA Loop Controller."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from atc.core.events import EventBus
from atc.qa.loop import QALoopController, TestFailure, parse_pytest_failures
from atc.state.db import (
    _SCHEMA_SQL,
    create_project,
    create_qa_loop_run,
    get_connection,
    get_latest_qa_loop_run,
    get_prs_needing_qa,
    run_migrations,
    update_pr_qa_status,
    upsert_github_pr,
)

# ---------------------------------------------------------------------------
# parse_pytest_failures — pure unit tests, no DB
# ---------------------------------------------------------------------------


class TestParsePytestFailures:
    def test_empty_output(self) -> None:
        assert parse_pytest_failures("") == []

    def test_single_failure(self) -> None:
        output = "FAILED tests/unit/test_foo.py::TestFoo::test_bar - AssertionError: 1 != 2"
        failures = parse_pytest_failures(output)
        assert len(failures) == 1
        assert failures[0].test_id == "tests/unit/test_foo.py::TestFoo::test_bar"
        assert failures[0].error == "AssertionError: 1 != 2"

    def test_multiple_failures(self) -> None:
        output = (
            "FAILED tests/a.py::test_one - ValueError: bad\n"
            "some other output\n"
            "FAILED tests/b.py::test_two - TypeError: oops\n"
        )
        failures = parse_pytest_failures(output)
        assert len(failures) == 2
        assert failures[0].test_id == "tests/a.py::test_one"
        assert failures[1].test_id == "tests/b.py::test_two"

    def test_failure_without_dash_separator(self) -> None:
        output = "FAILED tests/unit/test_foo.py::test_bar"
        failures = parse_pytest_failures(output)
        assert len(failures) == 1
        assert failures[0].test_id == "tests/unit/test_foo.py::test_bar"
        assert failures[0].error == ""

    def test_non_failure_lines_ignored(self) -> None:
        output = (
            "collected 10 items\n"
            "tests/unit/test_foo.py .......F..\n"
            "FAILED tests/unit/test_foo.py::test_bar - AssertionError\n"
            "1 failed, 9 passed in 0.42s\n"
        )
        failures = parse_pytest_failures(output)
        assert len(failures) == 1

    def test_passed_output_no_failures(self) -> None:
        output = "10 passed in 0.42s\n"
        assert parse_pytest_failures(output) == []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def ws_hub() -> MagicMock:
    hub = MagicMock()
    hub.broadcast = AsyncMock()
    return hub


@pytest.fixture
async def db():  # type: ignore[no-untyped-def]
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


# ---------------------------------------------------------------------------
# DB helpers — get_prs_needing_qa, update_pr_qa_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestQAStatusHelpers:
    async def test_get_prs_needing_qa_pending(self, db) -> None:  # type: ignore[no-untyped-def]
        project = await create_project(db, "proj", repo_path="/repo")
        await upsert_github_pr(db, "repo#1", project.id, 1, status="open")
        prs = await get_prs_needing_qa(db)
        assert len(prs) == 1
        assert prs[0].qa_status == "pending"

    async def test_get_prs_needing_qa_needs_rerun(self, db) -> None:  # type: ignore[no-untyped-def]
        project = await create_project(db, "proj", repo_path="/repo")
        await upsert_github_pr(db, "repo#1", project.id, 1, status="open")
        await update_pr_qa_status(db, "repo#1", "needs_rerun")
        prs = await get_prs_needing_qa(db)
        assert len(prs) == 1

    async def test_get_prs_needing_qa_excludes_running(self, db) -> None:  # type: ignore[no-untyped-def]
        project = await create_project(db, "proj", repo_path="/repo")
        await upsert_github_pr(db, "repo#1", project.id, 1, status="open")
        await update_pr_qa_status(db, "repo#1", "running")
        prs = await get_prs_needing_qa(db)
        assert prs == []

    async def test_get_prs_needing_qa_excludes_passed(self, db) -> None:  # type: ignore[no-untyped-def]
        project = await create_project(db, "proj", repo_path="/repo")
        await upsert_github_pr(db, "repo#1", project.id, 1, status="open")
        await update_pr_qa_status(db, "repo#1", "passed")
        prs = await get_prs_needing_qa(db)
        assert prs == []

    async def test_get_prs_needing_qa_filter_by_project(self, db) -> None:  # type: ignore[no-untyped-def]
        p1 = await create_project(db, "p1", repo_path="/r1")
        p2 = await create_project(db, "p2", repo_path="/r2")
        await upsert_github_pr(db, "repo#1", p1.id, 1, status="open")
        await upsert_github_pr(db, "repo#2", p2.id, 2, status="open")
        prs = await get_prs_needing_qa(db, project_id=p1.id)
        assert len(prs) == 1
        assert prs[0].project_id == p1.id


# ---------------------------------------------------------------------------
# QALoopRun CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestQALoopRunCRUD:
    async def test_create_and_fetch(self, db) -> None:  # type: ignore[no-untyped-def]
        project = await create_project(db, "proj", repo_path="/repo")
        await upsert_github_pr(db, "repo#1", project.id, 1, status="open")

        run = await create_qa_loop_run(db, project.id, "repo#1", iteration=1)
        assert run.status == "running"
        assert run.iteration == 1
        assert run.failure_count == 0

        latest = await get_latest_qa_loop_run(db, "repo#1")
        assert latest is not None
        assert latest.id == run.id

    async def test_get_latest_returns_highest_iteration(self, db) -> None:  # type: ignore[no-untyped-def]
        project = await create_project(db, "proj", repo_path="/repo")
        await upsert_github_pr(db, "repo#1", project.id, 1, status="open")

        await create_qa_loop_run(db, project.id, "repo#1", iteration=1)
        run2 = await create_qa_loop_run(db, project.id, "repo#1", iteration=2)

        latest = await get_latest_qa_loop_run(db, "repo#1")
        assert latest is not None
        assert latest.id == run2.id

    async def test_get_latest_none_for_unknown_pr(self, db) -> None:  # type: ignore[no-untyped-def]
        result = await get_latest_qa_loop_run(db, "nonexistent#999")
        assert result is None


# ---------------------------------------------------------------------------
# QALoopController._poll_all — picks up pending PRs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestQALoopControllerPoll:
    async def test_poll_all_sets_running_and_spawns_task(
        self, db, event_bus: EventBus, ws_hub: MagicMock
    ) -> None:
        project = await create_project(db, "proj", repo_path="/repo")
        await upsert_github_pr(db, "repo#1", project.id, 1, status="open")

        ctrl = QALoopController(db, event_bus, ws_hub=ws_hub, max_iterations=1)

        # Patch _process_pr_safe to a no-op so the actual test run is skipped.
        called: list[str] = []

        async def fake_process(pr_id: str, project_id: str) -> None:
            called.append(pr_id)

        ctrl._process_pr_safe = fake_process  # type: ignore[method-assign]

        await ctrl._poll_all()
        # Give the spawned task a chance to run.
        await asyncio.sleep(0)

        # PR should now be 'running' in the DB.
        cursor = await db.execute(
            "SELECT qa_status FROM github_prs WHERE id = 'repo#1'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "running"

        assert "repo#1" in called

    async def test_poll_all_skips_running_prs(
        self, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "proj", repo_path="/repo")
        await upsert_github_pr(db, "repo#1", project.id, 1, status="open")
        await update_pr_qa_status(db, "repo#1", "running")

        ctrl = QALoopController(db, event_bus)
        called: list[str] = []

        async def fake_process(pr_id: str, project_id: str) -> None:
            called.append(pr_id)

        ctrl._process_pr_safe = fake_process  # type: ignore[method-assign]

        await ctrl._poll_all()
        await asyncio.sleep(0)

        assert called == []

    async def test_poll_all_skips_prs_without_project_id(
        self, db, event_bus: EventBus
    ) -> None:
        # Insert a PR with NULL project_id directly.
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        await db.execute(
            "INSERT INTO github_prs (id, project_id, number, status, updated_at)"
            " VALUES ('noproj#1', NULL, 1, 'open', ?)",
            (now,),
        )
        await db.commit()

        ctrl = QALoopController(db, event_bus)
        called: list[str] = []

        async def fake_process(pr_id: str, project_id: str) -> None:
            called.append(pr_id)

        ctrl._process_pr_safe = fake_process  # type: ignore[method-assign]

        await ctrl._poll_all()
        await asyncio.sleep(0)

        assert called == []


# ---------------------------------------------------------------------------
# QALoopController._process_pr — green path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestQALoopControllerGreenPath:
    async def test_process_pr_passes_on_green_tests(
        self, db, event_bus: EventBus, ws_hub: MagicMock
    ) -> None:
        project = await create_project(db, "proj", repo_path="/repo")
        await upsert_github_pr(db, "repo#1", project.id, 1, status="open")
        await update_pr_qa_status(db, "repo#1", "running")

        ctrl = QALoopController(
            db, event_bus, ws_hub=ws_hub, max_iterations=3, poll_interval=0.01
        )

        # Stub test runner to return no failures.
        async def green_tests(repo_path: str) -> tuple[list[TestFailure], str]:
            return [], "5 passed in 0.1s"

        ctrl._run_tests = green_tests  # type: ignore[method-assign]

        published: list[tuple[str, dict]] = []
        orig_publish = event_bus.publish

        async def capture_publish(event: str, payload: dict | None = None) -> None:
            published.append((event, payload or {}))
            await orig_publish(event, payload)

        event_bus.publish = capture_publish  # type: ignore[method-assign]

        await ctrl._process_pr("repo#1", project.id)

        # PR should be passed.
        cursor = await db.execute(
            "SELECT qa_status FROM github_prs WHERE id = 'repo#1'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "passed"

        # Should have written a qa_loop_run row.
        run = await get_latest_qa_loop_run(db, "repo#1")
        assert run is not None
        assert run.status == "passed"
        assert run.failure_count == 0
        assert run.iteration == 1

        # Should have published qa_loop_passed.
        events = [e for e, _ in published]
        assert "qa_loop_passed" in events

        # Should have broadcast to ws.
        ws_hub.broadcast.assert_called()
        channel = ws_hub.broadcast.call_args_list[-1][0][0]
        assert channel == f"qa:{project.id}"


# ---------------------------------------------------------------------------
# QALoopController._process_pr — failure + task creation path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestQALoopControllerFailurePath:
    async def test_creates_task_graphs_for_failures(
        self, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "proj", repo_path="/repo")
        await upsert_github_pr(db, "repo#1", project.id, 1, status="open")
        await update_pr_qa_status(db, "repo#1", "running")

        ctrl = QALoopController(
            db, event_bus, max_iterations=1, task_wait_timeout=0.1
        )

        failures = [
            TestFailure("tests/test_a.py::test_one", "AssertionError"),
            TestFailure("tests/test_b.py::test_two", "ValueError"),
        ]

        async def failing_tests(repo_path: str) -> tuple[list[TestFailure], str]:
            return failures, "FAILED tests/test_a.py::test_one - AssertionError\n"

        ctrl._run_tests = failing_tests  # type: ignore[method-assign]

        await ctrl._process_pr("repo#1", project.id)

        # Should have escalated after 1 iteration (max_iterations=1).
        cursor = await db.execute(
            "SELECT qa_status FROM github_prs WHERE id = 'repo#1'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "escalated"

        # Should have created task_graphs rows.
        cursor = await db.execute(
            "SELECT COUNT(*) FROM task_graphs WHERE project_id = ?",
            (project.id,),
        )
        count_row = await cursor.fetchone()
        assert count_row is not None
        assert count_row[0] == 2

    async def test_escalates_when_no_progress(
        self, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "proj", repo_path="/repo")
        await upsert_github_pr(db, "repo#1", project.id, 1, status="open")
        await update_pr_qa_status(db, "repo#1", "running")

        ctrl = QALoopController(
            db, event_bus, max_iterations=5, task_wait_timeout=0.01
        )

        call_count = 0

        async def stubborn_tests(repo_path: str) -> tuple[list[TestFailure], str]:
            nonlocal call_count
            call_count += 1
            return [TestFailure("tests/test_x.py::test_y", "AssertionError")], "FAILED\n"

        ctrl._run_tests = stubborn_tests  # type: ignore[method-assign]

        await ctrl._process_pr("repo#1", project.id)

        # Should escalate after 2 iterations: iteration 1 creates tasks,
        # task wait times out (0.01s), so we escalate.
        cursor = await db.execute(
            "SELECT qa_status FROM github_prs WHERE id = 'repo#1'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "escalated"

    async def test_escalates_on_missing_repo_path(
        self, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "proj")  # no repo_path
        await upsert_github_pr(db, "repo#1", project.id, 1, status="open")
        await update_pr_qa_status(db, "repo#1", "running")

        ctrl = QALoopController(db, event_bus, max_iterations=3)

        await ctrl._process_pr("repo#1", project.id)

        cursor = await db.execute(
            "SELECT qa_status FROM github_prs WHERE id = 'repo#1'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "failed"

    async def test_publishes_qa_loop_escalated(
        self, db, event_bus: EventBus
    ) -> None:
        project = await create_project(db, "proj", repo_path="/repo")
        await upsert_github_pr(db, "repo#1", project.id, 1, status="open")
        await update_pr_qa_status(db, "repo#1", "running")

        ctrl = QALoopController(
            db, event_bus, max_iterations=1, task_wait_timeout=0.01
        )

        async def failing_tests(repo_path: str) -> tuple[list[TestFailure], str]:
            return [TestFailure("tests/test_z.py::test_q", "Error")], "FAILED\n"

        ctrl._run_tests = failing_tests  # type: ignore[method-assign]

        received: list[tuple[str, dict]] = []
        orig = event_bus.publish

        async def capture(event: str, payload: dict | None = None) -> None:
            received.append((event, payload or {}))
            await orig(event, payload)

        event_bus.publish = capture  # type: ignore[method-assign]

        await ctrl._process_pr("repo#1", project.id)

        escalated = [p for e, p in received if e == "qa_loop_escalated"]
        assert len(escalated) == 1
        assert escalated[0]["pr_id"] == "repo#1"
        assert escalated[0]["failure_count"] == 1


# ---------------------------------------------------------------------------
# QALoopController start / stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestQALoopControllerLifecycle:
    async def test_start_creates_task(self, db, event_bus: EventBus) -> None:
        ctrl = QALoopController(db, event_bus, poll_interval=9999.0)
        assert ctrl._task is None
        await ctrl.start()
        assert ctrl._task is not None
        await ctrl.stop()
        assert ctrl._task is None

    async def test_start_idempotent(self, db, event_bus: EventBus) -> None:
        ctrl = QALoopController(db, event_bus, poll_interval=9999.0)
        await ctrl.start()
        task_ref = ctrl._task
        await ctrl.start()
        assert ctrl._task is task_ref  # same task, not replaced
        await ctrl.stop()

    async def test_stop_without_start_is_safe(
        self, db, event_bus: EventBus
    ) -> None:
        ctrl = QALoopController(db, event_bus)
        await ctrl.stop()  # should not raise

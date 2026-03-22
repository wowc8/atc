"""QA Loop Controller — stateless test runner and task creator.

Polls ``github_prs`` for rows with ``qa_status IN ('pending', 'needs_rerun')``,
runs the project's test suite, writes per-iteration results to
``qa_loop_runs``, creates ``task_graphs`` rows for each failure so that
Leader/Aces can fix them, and re-runs until all tests pass or the iteration
budget is exhausted.

Loop contract
-------------
- All persistent state lives in the DB — no in-memory bookkeeping.
- On success:   ``qa_status = 'passed'``,    event ``qa_loop_passed``.
- On escalation: ``qa_status = 'escalated'``, event ``qa_loop_escalated``
  with full context for the Tower.
- Escalation triggers: max iterations reached, or failure count did not
  decrease between two consecutive runs (stuck), or task-wait timed out.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite

    from atc.api.ws.hub import WsHub
    from atc.core.events import EventBus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Failure parsing
# ---------------------------------------------------------------------------


@dataclass
class TestFailure:
    """A single test failure extracted from pytest output."""

    test_id: str  # e.g. "tests/unit/test_foo.py::TestFoo::test_bar"
    error: str  # short error description from the FAILED line


def parse_pytest_failures(output: str) -> list[TestFailure]:
    """Parse ``pytest --tb=short -q`` output and return failure entries.

    Looks for lines starting with ``FAILED `` and extracts the test node ID
    and the error description that follows the `` - `` separator.

    Example input line::

        FAILED tests/unit/test_foo.py::TestFoo::test_bar - AssertionError: x != y
    """
    failures: list[TestFailure] = []
    for line in output.splitlines():
        if line.startswith("FAILED "):
            rest = line[7:]
            parts = rest.split(" - ", 1)
            test_id = parts[0].strip()
            error = parts[1].strip() if len(parts) > 1 else ""
            failures.append(TestFailure(test_id=test_id, error=error))
    return failures


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


class QALoopController:
    """Stateless test runner + task creator.

    Runs in a background asyncio loop.  Never carries QA-cycle state in
    memory — everything lives in the DB (``qa_status`` on ``github_prs``,
    per-iteration rows in ``qa_loop_runs``).
    """

    poll_interval: float = 30.0
    task_poll_interval: float = 10.0

    def __init__(
        self,
        db: aiosqlite.Connection,
        event_bus: EventBus,
        *,
        ws_hub: WsHub | None = None,
        max_iterations: int = 5,
        poll_interval: float | None = None,
        task_poll_interval: float | None = None,
        test_timeout: float = 300.0,
        task_wait_timeout: float = 3600.0,
    ) -> None:
        self._db = db
        self._event_bus = event_bus
        self._ws_hub = ws_hub
        self._max_iterations = max_iterations
        if poll_interval is not None:
            self.poll_interval = poll_interval
        if task_poll_interval is not None:
            self.task_poll_interval = task_poll_interval
        self._test_timeout = test_timeout
        self._task_wait_timeout = task_wait_timeout
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background polling loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._poll_loop(), name="qa_loop_poll")
        logger.info(
            "QALoopController started (interval=%.0fs, max_iterations=%d)",
            self.poll_interval,
            self._max_iterations,
        )

    async def stop(self) -> None:
        """Stop the background polling loop."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            logger.info("QALoopController stopped")

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._poll_all()
            except Exception:
                logger.exception("QALoopController poll failed")
            await asyncio.sleep(self.poll_interval)

    async def _poll_all(self) -> None:
        """Find PRs that need QA and spawn a processing task for each.

        Setting ``qa_status = 'running'`` *before* spawning the task acts as
        a DB-level mutex: the next poll cycle will not pick up the same PR.
        """
        cursor = await self._db.execute(
            "SELECT id, project_id FROM github_prs"
            " WHERE qa_status IN ('pending', 'needs_rerun')",
        )
        rows = await cursor.fetchall()
        for row in rows:
            pr_id = str(row[0])
            project_id = str(row[1]) if row[1] else None
            if project_id is None:
                logger.warning("PR %s has no project_id, skipping", pr_id)
                continue
            # Claim the PR before yielding control.
            await self._set_pr_qa_status(pr_id, "running")
            asyncio.create_task(
                self._process_pr_safe(pr_id, project_id),
                name=f"qa_loop:{pr_id}",
            )

    # ------------------------------------------------------------------
    # Per-PR cycle
    # ------------------------------------------------------------------

    async def _process_pr_safe(self, pr_id: str, project_id: str) -> None:
        """Wrapper that ensures qa_status is never left as 'running' on error."""
        try:
            await self._process_pr(pr_id, project_id)
        except Exception:
            logger.exception("QA loop failed unexpectedly for PR %s", pr_id)
            await self._set_pr_qa_status(pr_id, "failed")

    async def _process_pr(self, pr_id: str, project_id: str) -> None:
        """Run the full QA iteration loop for a single PR."""
        cursor = await self._db.execute(
            "SELECT repo_path FROM projects WHERE id = ?",
            (project_id,),
        )
        row = await cursor.fetchone()
        if row is None or row[0] is None:
            logger.warning("PR %s: project %s has no repo_path", pr_id, project_id)
            await self._set_pr_qa_status(pr_id, "failed")
            return

        repo_path: str = str(row[0])
        prev_failure_count: int | None = None
        last_failures: list[TestFailure] = []
        last_test_output = ""

        for iteration in range(1, self._max_iterations + 1):
            logger.info(
                "QA loop: PR %s iteration %d/%d",
                pr_id,
                iteration,
                self._max_iterations,
            )

            run = await self._create_run(project_id, pr_id, iteration)
            failures, test_output = await self._run_tests(repo_path)
            last_failures = failures
            last_test_output = test_output
            failure_count = len(failures)

            await self._update_run(
                run_id=run,
                status="passed" if failure_count == 0 else "failed",
                failure_count=failure_count,
                test_output=test_output,
            )

            if failure_count == 0:
                logger.info("QA loop: PR %s PASSED on iteration %d", pr_id, iteration)
                await self._set_pr_qa_status(pr_id, "passed")
                await self._event_bus.publish(
                    "qa_loop_passed",
                    {"pr_id": pr_id, "project_id": project_id, "iterations": iteration},
                )
                await self._broadcast(
                    project_id,
                    {"type": "qa_passed", "pr_id": pr_id, "iterations": iteration},
                )
                return

            # Progress check (from iteration 2 onwards).
            if prev_failure_count is not None and failure_count >= prev_failure_count:
                logger.warning(
                    "QA loop: PR %s no progress (prev=%d, now=%d), escalating",
                    pr_id,
                    prev_failure_count,
                    failure_count,
                )
                await self._escalate(pr_id, project_id, last_failures, last_test_output)
                return

            prev_failure_count = failure_count

            # Create one TaskGraph row per failure for the Leader to dispatch.
            task_graph_ids = await self._create_fix_tasks(
                pr_id, project_id, failures, iteration
            )
            await self._event_bus.publish(
                "qa_loop_tasks_created",
                {
                    "pr_id": pr_id,
                    "project_id": project_id,
                    "task_graph_ids": task_graph_ids,
                    "iteration": iteration,
                    "failure_count": failure_count,
                },
            )
            await self._broadcast(
                project_id,
                {
                    "type": "qa_tasks_created",
                    "pr_id": pr_id,
                    "iteration": iteration,
                    "failure_count": failure_count,
                    "task_graph_ids": task_graph_ids,
                },
            )

            # Wait for Aces to finish fixing.
            completed = await self._wait_for_tasks(task_graph_ids)
            if not completed:
                logger.warning(
                    "QA loop: PR %s task wait timed out after %.0fs, escalating",
                    pr_id,
                    self._task_wait_timeout,
                )
                await self._escalate(pr_id, project_id, last_failures, last_test_output)
                return

            # Loop back to re-run tests with the fixes applied.

        # All iterations exhausted without reaching green.
        logger.warning(
            "QA loop: PR %s exhausted %d iterations, escalating",
            pr_id,
            self._max_iterations,
        )
        await self._escalate(pr_id, project_id, last_failures, last_test_output)

    # ------------------------------------------------------------------
    # Test runner
    # ------------------------------------------------------------------

    async def _run_tests(self, repo_path: str) -> tuple[list[TestFailure], str]:
        """Run pytest in *repo_path* and return ``(failures, raw_output)``."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3",
                "-m",
                "pytest",
                "--tb=short",
                "-q",
                "--no-header",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=repo_path,
            )
        except OSError as exc:
            logger.error("QA loop: failed to spawn test runner: %s", exc)
            return [], f"Failed to spawn test runner: {exc}"

        try:
            stdout_bytes, _ = await asyncio.wait_for(
                proc.communicate(), timeout=self._test_timeout
            )
        except TimeoutError:
            proc.kill()
            return [], f"Test run timed out after {self._test_timeout:.0f}s"

        output = stdout_bytes.decode("utf-8", errors="replace")
        return parse_pytest_failures(output), output

    # ------------------------------------------------------------------
    # Task management
    # ------------------------------------------------------------------

    async def _create_fix_tasks(
        self,
        pr_id: str,
        project_id: str,
        failures: list[TestFailure],
        iteration: int,
    ) -> list[str]:
        """Create one task_graphs row per failure and return the IDs."""
        from atc.state.db import create_task_graph

        ids: list[str] = []
        for failure in failures:
            tg = await create_task_graph(
                self._db,
                project_id,
                title=f"Fix failing test: {failure.test_id}",
                description=(
                    f"QA iteration {iteration} — PR: {pr_id}\n"
                    f"Error: {failure.error}"
                ),
            )
            ids.append(tg.id)
        return ids

    async def _wait_for_tasks(self, task_graph_ids: list[str]) -> bool:
        """Wait until all task graphs reach a terminal state (done or error).

        Returns ``True`` when all are terminal, ``False`` on timeout.
        """
        if not task_graph_ids:
            return True

        from atc.state.db import get_task_graph

        terminal = frozenset({"done", "error"})
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._task_wait_timeout

        while loop.time() < deadline:
            all_terminal = True
            for tg_id in task_graph_ids:
                tg = await get_task_graph(self._db, tg_id)
                if tg is not None and tg.status not in terminal:
                    all_terminal = False
                    break
            if all_terminal:
                return True
            await asyncio.sleep(self.task_poll_interval)

        return False

    # ------------------------------------------------------------------
    # Escalation
    # ------------------------------------------------------------------

    async def _escalate(
        self,
        pr_id: str,
        project_id: str,
        failures: list[TestFailure],
        test_output: str,
    ) -> None:
        """Mark the PR as escalated and notify Tower via event bus + WebSocket."""
        await self._set_pr_qa_status(pr_id, "escalated")
        context: dict[str, Any] = {
            "pr_id": pr_id,
            "project_id": project_id,
            "failure_count": len(failures),
            "failures": [
                {"test_id": f.test_id, "error": f.error} for f in failures
            ],
            "test_output_tail": test_output[-2000:] if test_output else "",
        }
        await self._event_bus.publish("qa_loop_escalated", context)
        await self._broadcast(project_id, {"type": "qa_escalated", **context})
        logger.error(
            "QA loop: PR %s escalated with %d unresolved failures",
            pr_id,
            len(failures),
        )

    # ------------------------------------------------------------------
    # DB helpers (inline to avoid importing from db.py at module level)
    # ------------------------------------------------------------------

    async def _set_pr_qa_status(self, pr_id: str, qa_status: str) -> None:
        await self._db.execute(
            "UPDATE github_prs SET qa_status = ?, updated_at = ? WHERE id = ?",
            (qa_status, datetime.now(UTC).isoformat(), pr_id),
        )
        await self._db.commit()

    async def _create_run(
        self, project_id: str, pr_id: str, iteration: int
    ) -> str:
        """Insert a qa_loop_runs row and return its id."""
        run_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        await self._db.execute(
            """INSERT INTO qa_loop_runs
               (id, project_id, pr_id, iteration, status, failure_count,
                test_output, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'running', 0, NULL, ?, ?)""",
            (run_id, project_id, pr_id, iteration, now, now),
        )
        await self._db.commit()
        return run_id

    async def _update_run(
        self,
        *,
        run_id: str,
        status: str,
        failure_count: int,
        test_output: str,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        await self._db.execute(
            "UPDATE qa_loop_runs"
            " SET status = ?, failure_count = ?, test_output = ?, updated_at = ?"
            " WHERE id = ?",
            (status, failure_count, test_output, now, run_id),
        )
        await self._db.commit()

    async def _broadcast(self, project_id: str, payload: dict[str, Any]) -> None:
        if self._ws_hub is not None:
            await self._ws_hub.broadcast(f"qa:{project_id}", payload)

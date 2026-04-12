"""Leader orchestrator — spawns and manages Ace sessions for task graph entries.

The orchestrator is the main runtime loop for a Leader session.  After the
decomposer creates task graph entries, the orchestrator:

  1. Finds ready tasks (no unfinished dependencies)
  2. Spawns Ace sessions for each ready task
  3. Assigns task graph entries to their Ace sessions
  4. Monitors Ace progress via the event bus
  5. Marks tasks done when Aces complete
  6. Reports overall progress back to Tower
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from atc.agents.deploy import AceDeploySpec, cleanup_deployed_files
from atc.agents.factory import get_launch_command
from atc.leader.context_package import build_context_package
from atc.leader.decomposer import get_completion_status, get_ready_tasks
from atc.session.ace import create_ace, destroy_ace, start_ace
from atc.state import db as db_ops
from atc.tracking.resources import ResourceGovernor

# Global active Ace counter — shared across ALL orchestrator instances so
# the per-machine limit is enforced even when multiple Leaders spawn Aces
# simultaneously (each would otherwise see active_count=0).
_GLOBAL_ACTIVE_ACES: int = 0
_GLOBAL_LOCK = None  # asyncio.Lock, initialized lazily


async def _get_global_lock() -> "asyncio.Lock":
    import asyncio
    global _GLOBAL_LOCK
    if _GLOBAL_LOCK is None:
        _GLOBAL_LOCK = asyncio.Lock()
    return _GLOBAL_LOCK

if TYPE_CHECKING:
    from pathlib import Path

    import aiosqlite

    from atc.core.events import EventBus

logger = logging.getLogger(__name__)


@dataclass
class AceAssignment:
    """Tracks the association between an Ace session and a task graph entry."""

    ace_session_id: str
    task_graph_id: str
    task_title: str
    assignment_id: str = ""  # idempotency key for assign_task
    status: str = "assigned"  # assigned|working|done|failed
    deployed_root: Path | None = None  # staging dir for cleanup


@dataclass
class LeaderOrchestrator:
    """Manages Ace session lifecycle for a project's task graph.

    One orchestrator per active Leader session.  It maintains a mapping
    of task_graph_id → ace_session_id and coordinates the work.
    """

    project_id: str
    leader_id: str
    conn: aiosqlite.Connection
    event_bus: EventBus | None = None
    assignments: dict[str, AceAssignment] = field(default_factory=dict)
    _max_concurrent_aces: int = 3
    _governor: ResourceGovernor = field(default_factory=ResourceGovernor)

    async def spawn_aces_for_ready_tasks(self) -> list[AceAssignment]:
        """Find ready tasks and spawn Ace sessions for them.

        Respects the max concurrent Aces limit. Returns a list of new
        assignments created.
        """
        task_graphs = await db_ops.list_task_graphs(
            self.conn,
            project_id=self.project_id,
        )

        ready = get_ready_tasks(task_graphs)
        if not ready:
            return []

        # Use GLOBAL active count so the limit is enforced across all Leaders/projects
        global _GLOBAL_ACTIVE_ACES
        lock = await _get_global_lock()
        async with lock:
            available_slots = self._governor.available_ace_slots(_GLOBAL_ACTIVE_ACES)
            if available_slots == 0:
                logger.info(
                    "Leader %s: no Ace slots available (global_active=%d, system load check)",
                    self.leader_id,
                    _GLOBAL_ACTIVE_ACES,
                )
                return []
            # Reserve slots atomically before spawning
            slots_to_use = min(available_slots, len([tg for tg in get_ready_tasks(task_graphs) if tg.id not in self.assignments]))
            _GLOBAL_ACTIVE_ACES += slots_to_use

        new_assignments: list[AceAssignment] = []
        unassigned_ready = [tg for tg in ready if tg.id not in self.assignments]
        for tg in unassigned_ready[:slots_to_use]:
            assignment = await self._spawn_ace_for_task(tg.id, tg.title, tg.description)
            if assignment is not None:
                new_assignments.append(assignment)
            else:
                # Spawn failed — return the reserved slot to the global counter
                _GLOBAL_ACTIVE_ACES = max(0, _GLOBAL_ACTIVE_ACES - 1)

        return new_assignments

    async def _spawn_ace_for_task(
        self,
        task_graph_id: str,
        title: str,
        description: str | None,
    ) -> AceAssignment | None:
        """Spawn a single Ace session and assign it to a task graph entry.

        Uses ``assign_task`` for idempotent, state-machine-guarded
        assignment.  If the same ``assignment_id`` is used twice the
        second call is a no-op.

        Deploys config files (CLAUDE.md, hooks) via ``deploy_ace_files``
        before launching the tmux pane so Claude Code reads the task
        instructions automatically.
        """
        ace_name = f"ace-{title[:30]}"

        # Generate a deterministic assignment_id for idempotency.
        # The leader_id + task_graph_id combination ensures that the
        # same leader assigning the same task produces the same key.
        idempotency_key = f"{self.leader_id}:{task_graph_id}"

        try:
            # Look up project metadata for the deploy spec
            project = await db_ops.get_project(self.conn, self.project_id)
            project_name = project.name if project else ""
            repo_path = project.repo_path if project else None
            github_repo = project.github_repo if project else None

            # Fetch inherited context entries for the Ace. Use a preview id only
            # for context assembly; create_ace will deploy hooks/config with the real
            # session id so callbacks target the live session instead of a ghost id.
            context_entries: list[dict[str, Any]] = []
            with contextlib.suppress(Exception):
                ctx = await build_context_package(
                    self.conn,
                    self.project_id,
                    title,
                    session_id=f"preview:{task_graph_id}",
                    parent_session_id=self.leader_id,
                    scope="ace",
                )
                context_entries = ctx.get("context_entries", [])

            launch_cmd = get_launch_command(
                project.agent_provider if project else "claude_code",
            )

            session_id = await create_ace(
                self.conn,
                self.project_id,
                ace_name,
                task_id=task_graph_id,
                event_bus=self.event_bus,
                working_dir=repo_path,
                launch_command=launch_cmd,
                deploy_spec_kwargs={
                    "project_name": project_name,
                    "task_title": title,
                    "task_description": description,
                    "project_id": self.project_id,
                    "repo_path": repo_path,
                    "github_repo": github_repo,
                    "context_entries": context_entries,
                },
            )
        except Exception:
            logger.exception(
                "Failed to spawn Ace for task '%s' (graph %s)",
                title,
                task_graph_id,
            )
            return None

        # Idempotent assignment — creates record + transitions task to 'assigned'
        try:
            db_assignment, created = await db_ops.assign_task(
                self.conn,
                task_graph_id,
                session_id,
                idempotency_key,
            )
        except ValueError as exc:
            logger.warning(
                "Cannot assign task '%s' (graph %s): %s",
                title,
                task_graph_id,
                exc,
            )
            return None

        if not created:
            logger.info(
                "Leader %s: assignment for task '%s' already exists (idempotent no-op)",
                self.leader_id,
                title,
            )

        # Transition the task to in_progress now that the Ace is running
        await db_ops.update_task_graph_status(
            self.conn,
            task_graph_id,
            "in_progress",
        )

        assignment = AceAssignment(
            ace_session_id=session_id,
            task_graph_id=task_graph_id,
            task_title=title,
            assignment_id=idempotency_key,
            status="assigned",
            deployed_root=Path("/tmp/atc-agents") / session_id,
        )
        self.assignments[task_graph_id] = assignment

        logger.info(
            "Leader %s: spawned Ace %s for task '%s'",
            self.leader_id,
            session_id,
            title,
        )

        if self.event_bus:
            await self.event_bus.publish(
                "leader_ace_spawned",
                {
                    "leader_id": self.leader_id,
                    "project_id": self.project_id,
                    "session_id": session_id,
                    "task_graph_id": task_graph_id,
                    "task_title": title,
                    "assignment_id": idempotency_key,
                },
            )

        return assignment

    async def send_instruction_to_ace(
        self,
        task_graph_id: str,
        instruction: str,
    ) -> None:
        """Send a work instruction to the Ace assigned to a task graph entry."""
        assignment = self.assignments.get(task_graph_id)
        if assignment is None:
            raise ValueError(f"No Ace assigned to task graph {task_graph_id}")

        await start_ace(
            self.conn,
            assignment.ace_session_id,
            instruction=instruction,
            event_bus=self.event_bus,
        )
        assignment.status = "working"

    async def mark_task_done(self, task_graph_id: str) -> None:
        """Mark a task graph entry as done and clean up its Ace session."""
        assignment = self.assignments.get(task_graph_id)

        # Update the assignment record status
        if assignment and assignment.assignment_id:
            with contextlib.suppress(ValueError):
                await db_ops.update_task_assignment_status(
                    self.conn,
                    assignment.assignment_id,
                    "done",
                )

        # Update task graph status — advance through intermediate states as needed.
        # The state machine requires assigned→in_progress→done, so we bridge the
        # gap if the task is still in an earlier state.
        tg = await db_ops.get_task_graph(self.conn, task_graph_id)
        if tg is not None and tg.status == "assigned":
            with contextlib.suppress(ValueError):
                await db_ops.update_task_graph_status(self.conn, task_graph_id, "in_progress")
        await db_ops.update_task_graph_status(
            self.conn,
            task_graph_id,
            "done",
        )

        # Bug #163: decrement the global counter unconditionally when a task is
        # marked done, regardless of whether this orchestrator instance has the
        # assignment in memory.  Orchestrators can be recreated after a server
        # restart or across requests, so self.assignments may be empty even
        # when real Aces were running.
        global _GLOBAL_ACTIVE_ACES
        lock = await _get_global_lock()
        async with lock:
            _GLOBAL_ACTIVE_ACES = max(0, _GLOBAL_ACTIVE_ACES - 1)
            logger.debug(
                "mark_task_done: decremented _GLOBAL_ACTIVE_ACES to %d (task %s)",
                _GLOBAL_ACTIVE_ACES,
                task_graph_id,
            )

        if assignment is not None:
            assignment.status = "done"

            # Destroy the Ace session (free resources)
            try:
                await destroy_ace(
                    self.conn,
                    assignment.ace_session_id,
                    event_bus=self.event_bus,
                )
            except Exception:
                logger.warning(
                    "Failed to destroy Ace %s for completed task '%s'",
                    assignment.ace_session_id,
                    assignment.task_title,
                )

            # Clean up deployed config files
            if assignment.deployed_root:
                with contextlib.suppress(Exception):
                    cleanup_deployed_files(assignment.deployed_root)

        if self.event_bus:
            await self.event_bus.publish(
                "leader_task_completed",
                {
                    "leader_id": self.leader_id,
                    "project_id": self.project_id,
                    "task_graph_id": task_graph_id,
                },
            )

    async def mark_task_failed(
        self,
        task_graph_id: str,
        *,
        reason: str | None = None,
    ) -> None:
        """Handle a failed task — update status and clean up Ace."""
        assignment = self.assignments.get(task_graph_id)

        # Bug #163: decrement unconditionally (same reasoning as mark_task_done).
        global _GLOBAL_ACTIVE_ACES
        lock = await _get_global_lock()
        async with lock:
            _GLOBAL_ACTIVE_ACES = max(0, _GLOBAL_ACTIVE_ACES - 1)
            logger.debug(
                "mark_task_failed: decremented _GLOBAL_ACTIVE_ACES to %d (task %s)",
                _GLOBAL_ACTIVE_ACES,
                task_graph_id,
            )

        # Update the assignment record status
        if assignment and assignment.assignment_id:
            with contextlib.suppress(ValueError):
                await db_ops.update_task_assignment_status(
                    self.conn,
                    assignment.assignment_id,
                    "failed",
                )

        # Transition to error, then back to todo so it can be retried
        with contextlib.suppress(ValueError):
            await db_ops.update_task_graph_status(
                self.conn,
                task_graph_id,
                "error",
            )
        with contextlib.suppress(ValueError):
            await db_ops.update_task_graph_status(
                self.conn,
                task_graph_id,
                "todo",
            )

        if assignment is not None:
            assignment.status = "failed"
            try:
                await destroy_ace(
                    self.conn,
                    assignment.ace_session_id,
                    event_bus=self.event_bus,
                )
            except Exception:
                logger.warning(
                    "Failed to destroy Ace %s for failed task '%s'",
                    assignment.ace_session_id,
                    assignment.task_title,
                )

            # Clean up deployed config files
            if assignment.deployed_root:
                with contextlib.suppress(Exception):
                    cleanup_deployed_files(assignment.deployed_root)

            # Remove assignment so the task can be re-assigned
            del self.assignments[task_graph_id]

        if self.event_bus:
            await self.event_bus.publish(
                "leader_task_failed",
                {
                    "leader_id": self.leader_id,
                    "project_id": self.project_id,
                    "task_graph_id": task_graph_id,
                    "reason": reason,
                },
            )

    async def get_progress(self) -> dict[str, Any]:
        """Return current progress summary for the Leader's task graph."""
        task_graphs = await db_ops.list_task_graphs(
            self.conn,
            project_id=self.project_id,
        )

        status = get_completion_status(task_graphs)
        status["assignments"] = [
            {
                "task_graph_id": a.task_graph_id,
                "ace_session_id": a.ace_session_id,
                "task_title": a.task_title,
                "status": a.status,
            }
            for a in self.assignments.values()
        ]
        status["leader_id"] = self.leader_id
        status["project_id"] = self.project_id

        return status

    async def cleanup(self) -> None:
        """Destroy all active Ace sessions (called on Leader shutdown)."""
        for assignment in list(self.assignments.values()):
            if assignment.status in ("assigned", "working"):
                try:
                    await destroy_ace(
                        self.conn,
                        assignment.ace_session_id,
                        event_bus=self.event_bus,
                    )
                except Exception:
                    logger.warning(
                        "Failed to destroy Ace %s during cleanup",
                        assignment.ace_session_id,
                    )
        self.assignments.clear()

    async def on_session_status_changed(self, data: dict[str, Any]) -> None:
        """Handle Ace session status changes (event bus callback).

        Detects when an Ace enters error/disconnected state and marks
        the corresponding task as failed for retry.
        """
        session_id = data.get("session_id", "")
        new_status = data.get("new_status", "")

        # Find which assignment this session belongs to
        for tg_id, assignment in self.assignments.items():
            if assignment.ace_session_id == session_id:
                if new_status == "error":
                    logger.warning(
                        "Ace %s for task '%s' entered error state",
                        session_id,
                        assignment.task_title,
                    )
                    await self.mark_task_failed(
                        tg_id,
                        reason="Ace session entered error state",
                    )
                elif new_status == "disconnected":
                    logger.warning(
                        "Ace %s for task '%s' disconnected",
                        session_id,
                        assignment.task_title,
                    )
                    await self.mark_task_failed(
                        tg_id,
                        reason="Ace session disconnected",
                    )
                break

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

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from atc.agents.deploy import AceDeploySpec, cleanup_deployed_files, deploy_ace_files
from atc.agents.factory import get_launch_command
from atc.leader.context_package import build_context_package
from atc.leader.decomposer import get_completion_status, get_ready_tasks
from atc.leader.dispatch import verify_ace_dispatch_delivery
from atc.runtime.health import ace_health
from atc.session.ace import create_ace, destroy_ace, start_ace
from atc.state import db as db_ops
from atc.state.transitions import LifecycleTransitionError
from atc.tracking.resources import ResourceGovernor

# Global active Ace counter — shared across ALL orchestrator instances so
# the per-machine limit is enforced even when multiple Leaders spawn Aces
# simultaneously (each would otherwise see active_count=0).
_GLOBAL_ACTIVE_ACES: int = 0
_GLOBAL_LOCK = None  # asyncio.Lock, initialized lazily


async def _get_global_lock() -> asyncio.Lock:
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
    startup_readiness_state: str = "startup_handshake_pending"
    dispatch_delivery_state: str = "queued_unverified"
    dispatch_verified: bool = False
    ace_reported_active: bool = False
    assignment_accepted: bool = False
    assignment_accepted_at: str | None = None
    artifact_ready: bool = False
    artifact_path: str | None = None
    artifact_kind: str | None = None
    artifact_reported_at: str | None = None
    blocker_reason: str | None = None
    last_activity_at: str | None = None
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
    blocked_transition_errors: list[dict[str, Any]] = field(default_factory=list)
    _max_concurrent_aces: int = 3
    _governor: ResourceGovernor = field(default_factory=ResourceGovernor)

    def _current_provider_default(self) -> str:
        app_state = getattr(getattr(self.conn, "_connection", None), "app_state", None)
        if app_state is not None and getattr(app_state, "settings", None) is not None:
            return app_state.settings.agent_provider.default
        from atc.config import load_settings

        return load_settings().agent_provider.default

    def _record_transition_block(self, exc: LifecycleTransitionError) -> None:
        """Keep the most recent structured transition blockers visible to callers."""
        detail = exc.to_detail()
        self.blocked_transition_errors.append(detail)
        self.blocked_transition_errors = self.blocked_transition_errors[-10:]
        logger.warning("Blocked lifecycle transition: %s", detail)

    async def spawn_aces_for_ready_tasks(self) -> list[AceAssignment]:
        """Find ready tasks and spawn Ace sessions for them.

        Respects the max concurrent Aces limit. Returns a list of new
        assignments created.
        """
        task_graphs = await db_ops.list_task_graphs(
            self.conn,
            project_id=self.project_id,
        )

        active_aces_by_task = await self._active_ace_sessions_by_task()
        ready = [tg for tg in get_ready_tasks(task_graphs) if tg.id not in active_aces_by_task]
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
            spawnable_count = len([tg for tg in ready if tg.id not in self.assignments])
            slots_to_use = min(available_slots, spawnable_count)
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

    async def spawn_ace_for_task(self, task_graph_id: str) -> AceAssignment | None:
        """Spawn or reuse an Ace assignment for one ready task graph entry."""
        task_graphs = await db_ops.list_task_graphs(
            self.conn,
            project_id=self.project_id,
        )
        task_by_id = {tg.id: tg for tg in task_graphs}
        task = task_by_id.get(task_graph_id)
        if task is None:
            raise ValueError(f"TaskGraph {task_graph_id} not found")

        active_aces_by_task = await self._active_ace_sessions_by_task()
        if task_graph_id in active_aces_by_task:
            existing = self.assignments.get(task_graph_id)
            if existing is not None:
                return existing
            active_assignment = next(
                (
                    assignment
                    for assignment in await db_ops.list_task_assignments(
                        self.conn,
                        task_graph_id=task_graph_id,
                    )
                    if assignment.status in {"assigned", "working"}
                    and assignment.ace_session_id == active_aces_by_task[task_graph_id]
                ),
                None,
            )
            if active_assignment is not None:
                restored = AceAssignment(
                    ace_session_id=active_assignment.ace_session_id,
                    task_graph_id=task_graph_id,
                    task_title=task.title,
                    assignment_id=active_assignment.assignment_id,
                    status=active_assignment.status,
                )
                self.assignments[task_graph_id] = restored
                return restored
            raise ValueError(
                f"Task {task_graph_id} already has an active Ace session "
                f"{active_aces_by_task[task_graph_id]}"
            )

        ready_ids = {tg.id for tg in get_ready_tasks(task_graphs)}
        if task_graph_id not in ready_ids:
            raise ValueError(f"Task {task_graph_id} is not ready for assignment")

        global _GLOBAL_ACTIVE_ACES
        lock = await _get_global_lock()
        reserved = False
        async with lock:
            if self._governor.available_ace_slots(_GLOBAL_ACTIVE_ACES) <= 0:
                raise ValueError("No Ace slots available")
            _GLOBAL_ACTIVE_ACES += 1
            reserved = True

        assignment = await self._spawn_ace_for_task(task.id, task.title, task.description)
        if assignment is None and reserved:
            _GLOBAL_ACTIVE_ACES = max(0, _GLOBAL_ACTIVE_ACES - 1)
        return assignment

    async def _active_ace_sessions_by_task(self) -> dict[str, str]:
        """Return active Ace sessions keyed by task id to preserve 1:1 pairing."""
        terminal_statuses = {"completed", "cancelled", "error", "disconnected"}
        sessions = await db_ops.list_sessions(
            self.conn,
            project_id=self.project_id,
            session_type="ace",
        )
        return {
            session.task_id: session.id
            for session in sessions
            if session.task_id and session.status not in terminal_statuses
        }

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

            provider_name = (
                project.agent_provider
                if project and project.agent_provider
                else self._current_provider_default()
            )

            session_id = await create_ace(
                self.conn,
                self.project_id,
                ace_name,
                task_id=task_graph_id,
                event_bus=self.event_bus,
                working_dir=repo_path,
                launch_command=get_launch_command(provider_name),
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

        # The DB assignment transition moves the task graph to assigned.
        # Do not promote to in_progress until Ace dispatch is verified.

        deployed = deploy_ace_files(
            AceDeploySpec(
                session_id=session_id,
                project_name=project_name,
                task_title=title,
                task_description=description,
                project_id=self.project_id,
                repo_path=repo_path,
                github_repo=github_repo,
                context_entries=context_entries,
            )
        )

        assignment = AceAssignment(
            ace_session_id=session_id,
            task_graph_id=task_graph_id,
            task_title=title,
            assignment_id=idempotency_key,
            status="assigned",
            deployed_root=deployed.root,
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

    async def _artifact_context_for_task(self, task_graph_id: str) -> str:
        """Return dependency artifact paths that should be routed to this task."""

        task = await db_ops.get_task_graph(self.conn, task_graph_id)
        dependencies = getattr(task, "dependencies", None) if task is not None else None
        if not dependencies:
            return ""
        assignments = await db_ops.list_task_assignments(self.conn)
        dependency_artifacts = [
            a
            for a in assignments
            if a.task_graph_id in dependencies and a.artifact_ready and a.artifact_path
        ]
        if not dependency_artifacts:
            return ""
        lines = [
            "Dependency artifact routing:",
            (
                "Use these canonical paths from completed dependency tasks instead "
                "of searching sibling worktrees."
            ),
        ]
        for artifact in dependency_artifacts:
            lines.append(
                f"- task_graph_id={artifact.task_graph_id} "
                f"kind={artifact.artifact_kind or 'artifact'} "
                f"path={artifact.artifact_path}"
            )
        return "\n".join(lines)

    async def send_instruction_to_ace(
        self,
        task_graph_id: str,
        instruction: str,
    ) -> Any:
        """Send a work instruction and promote work state only after verification."""
        assignment = self.assignments.get(task_graph_id)
        if assignment is None:
            raise ValueError(f"No Ace assigned to task graph {task_graph_id}")

        artifact_context = await self._artifact_context_for_task(task_graph_id)
        if artifact_context:
            instruction = f"{instruction}\n\n{artifact_context}"

        result = await start_ace(
            self.conn,
            assignment.ace_session_id,
            instruction=instruction,
            event_bus=self.event_bus,
        )
        verification = verify_ace_dispatch_delivery(result, task_graph_id=task_graph_id)
        assignment.dispatch_delivery_state = verification.dispatch_delivery_state
        assignment.dispatch_verified = verification.dispatch_verified
        assignment.startup_readiness_state = str(
            result.details.get("startup_readiness_state")
            or result.details.get("startup_state")
            or getattr(assignment, "startup_readiness_state", "startup_handshake_pending")
        )
        assignment.blocker_reason = verification.blocker_reason

        if assignment.assignment_id:
            await db_ops.update_task_assignment_startup_readiness(
                self.conn,
                assignment.assignment_id,
                startup_readiness_state=assignment.startup_readiness_state,
                blocker_reason=verification.blocker_reason,
                last_activity=False,
            )
            persisted_assignment = await db_ops.update_task_assignment_dispatch(
                self.conn,
                assignment.assignment_id,
                dispatch_delivery_state=verification.dispatch_delivery_state,
                dispatch_verified=verification.dispatch_verified,
                blocker_reason=verification.blocker_reason,
                last_activity=verification.ace_began_work,
            )
            if persisted_assignment is not None:
                assignment.last_activity_at = persisted_assignment.last_activity_at

        if not verification.dispatch_verified:
            assignment.status = "assigned"
            if self.event_bus:
                await self.event_bus.publish(
                    "leader_ace_dispatch_unverified",
                    {
                        "leader_id": self.leader_id,
                        "project_id": self.project_id,
                        "task_graph_id": task_graph_id,
                        "session_id": assignment.ace_session_id,
                        **verification.as_dict(),
                    },
                )
            return result

        assignment.status = "working"
        if assignment.assignment_id:
            try:
                await db_ops.update_task_assignment_status(
                    self.conn,
                    assignment.assignment_id,
                    "working",
                )
            except LifecycleTransitionError as exc:
                self._record_transition_block(exc)
                raise
            except ValueError:
                logger.debug(
                    "Assignment %s disappeared while instructing task %s",
                    assignment.assignment_id,
                    task_graph_id,
                )

        task_graph = await db_ops.get_task_graph(self.conn, task_graph_id)
        if task_graph is not None:
            if task_graph.status == "todo":
                task_graph = await db_ops.update_task_graph_status(
                    self.conn,
                    task_graph_id,
                    "assigned",
                )
            if task_graph is not None and task_graph.status == "assigned":
                await db_ops.update_task_graph_status(self.conn, task_graph_id, "in_progress")

        if self.event_bus:
            await self.event_bus.publish(
                "leader_ace_dispatch_verified",
                {
                    "leader_id": self.leader_id,
                    "project_id": self.project_id,
                    "task_graph_id": task_graph_id,
                    "session_id": assignment.ace_session_id,
                    **verification.as_dict(),
                },
            )
        return result

    async def mark_task_done(self, task_graph_id: str) -> None:
        """Mark a task graph entry as done and clean up its Ace session."""
        assignment = self.assignments.get(task_graph_id)

        # Update the assignment record status
        if assignment and assignment.assignment_id:
            try:
                db_assignment = await db_ops.get_task_assignment(
                    self.conn,
                    assignment.assignment_id,
                )
                if db_assignment is not None and db_assignment.status == "assigned":
                    await db_ops.update_task_assignment_status(
                        self.conn,
                        assignment.assignment_id,
                        "working",
                    )
                await db_ops.update_task_assignment_status(
                    self.conn,
                    assignment.assignment_id,
                    "done",
                )
            except LifecycleTransitionError as exc:
                self._record_transition_block(exc)
                raise
            except ValueError:
                logger.debug(
                    "Assignment %s disappeared while marking task %s done",
                    assignment.assignment_id,
                    task_graph_id,
                )

        # Update task graph status — advance through intermediate states as needed.
        # The state machine requires assigned→in_progress→done, so we bridge the
        # gap if the task is still in an earlier state.
        tg = await db_ops.get_task_graph(self.conn, task_graph_id)
        if tg is not None and tg.status == "assigned":
            try:
                await db_ops.update_task_graph_status(self.conn, task_graph_id, "in_progress")
            except LifecycleTransitionError as exc:
                self._record_transition_block(exc)
                raise
        try:
            await db_ops.update_task_graph_status(
                self.conn,
                task_graph_id,
                "done",
            )
        except LifecycleTransitionError as exc:
            self._record_transition_block(exc)
            raise

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
            try:
                await db_ops.update_task_assignment_status(
                    self.conn,
                    assignment.assignment_id,
                    "failed",
                )
            except LifecycleTransitionError as exc:
                self._record_transition_block(exc)
                raise
            except ValueError:
                logger.debug(
                    "Assignment %s disappeared while marking task %s failed",
                    assignment.assignment_id,
                    task_graph_id,
                )

        # Transition to error, then back to todo so it can be retried.
        # If the task is still assigned, bridge through in_progress first: the
        # state machine intentionally disallows assigned→todo and requires
        # explicit recovery transitions.
        tg = await db_ops.get_task_graph(self.conn, task_graph_id)
        try:
            if tg is not None and tg.status == "assigned":
                tg = await db_ops.update_task_graph_status(
                    self.conn,
                    task_graph_id,
                    "in_progress",
                )
            if tg is not None and tg.status == "in_progress":
                tg = await db_ops.update_task_graph_status(
                    self.conn,
                    task_graph_id,
                    "error",
                )
            if tg is not None and tg.status in {"error", "done"}:
                await db_ops.update_task_graph_status(
                    self.conn,
                    task_graph_id,
                    "todo",
                )
        except LifecycleTransitionError as exc:
            self._record_transition_block(exc)
            raise

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
        persisted_assignments = await db_ops.list_task_assignments(self.conn)
        assignment_rows = persisted_assignments or list(self.assignments.values())
        assignments = [
            {
                "task_graph_id": a.task_graph_id,
                "ace_session_id": a.ace_session_id,
                "task_title": getattr(a, "task_title", None),
                "status": a.status,
                "startup_readiness_state": getattr(
                    a, "startup_readiness_state", "startup_handshake_pending"
                ),
                "dispatch_delivery_state": a.dispatch_delivery_state,
                "dispatch_verified": a.dispatch_verified,
                "assignment_acceptance_state": self._assignment_acceptance_state(a),
                "ace_reported_active": getattr(a, "ace_reported_active", False),
                "assignment_accepted": getattr(a, "assignment_accepted", False),
                "assignment_accepted_at": getattr(a, "assignment_accepted_at", None),
                "artifact_ready": getattr(a, "artifact_ready", False),
                "artifact_path": getattr(a, "artifact_path", None),
                "artifact_kind": getattr(a, "artifact_kind", None),
                "artifact_reported_at": getattr(a, "artifact_reported_at", None),
                "blocker_reason": a.blocker_reason,
                "last_activity_at": a.last_activity_at,
                "last_provider_activity_at": a.last_activity_at,
                "last_ace_report_at": getattr(a, "assignment_accepted_at", None),
            }
            for a in assignment_rows
        ]
        ace_blockers = self._build_ace_blocker_summaries(assignments)
        status["assignments"] = assignments
        status["leader_id"] = self.leader_id
        status["project_id"] = self.project_id
        status["leader_state"] = self._leader_state(status, ace_blockers)
        status["handoff_verified"] = bool(status.get("total") or assignments)
        status["ace_blockers"] = ace_blockers
        status["tower_must_not"] = [
            "inspect_ace_pane",
            "recover_ace_directly",
            "message_ace_directly",
            "mark_ace_done_directly",
        ]
        status["tower_recommended_action"] = (
            "nudge_leader_to_resolve_ace_blockers"
            if ace_blockers
            else "wait_for_leader_or_completion_hook"
        )
        status["blocked_transition_errors"] = list(self.blocked_transition_errors)

        return status

    @staticmethod
    def _leader_state(status: dict[str, Any], ace_blockers: list[dict[str, Any]]) -> str:
        if status.get("all_done"):
            return "complete"
        if ace_blockers:
            return "ace_blocked"
        if status.get("in_progress") or status.get("todo") or status.get("total"):
            return "working"
        return "waiting_for_task_graph"

    @staticmethod
    def _build_ace_blocker_summaries(assignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        blockers: list[dict[str, Any]] = []
        for assignment in assignments:
            blocker_reason = assignment.get("blocker_reason")
            acceptance_state = assignment.get("assignment_acceptance_state")
            dispatch_verified = bool(assignment.get("dispatch_verified"))
            is_blocked = bool(blocker_reason) or (
                acceptance_state in {"blocked", "startup_blocked"}
            )
            if not is_blocked:
                continue
            blockers.append(
                {
                    "ace_id": assignment.get("ace_session_id"),
                    "ace_session_id": assignment.get("ace_session_id"),
                    "task_id": assignment.get("task_graph_id"),
                    "task_graph_id": assignment.get("task_graph_id"),
                    "blocker_reason": blocker_reason or acceptance_state or "ace_blocked",
                    "dispatch_verified": dispatch_verified,
                    "assignment_acceptance_state": acceptance_state,
                    "owner": "leader",
                    "leader_action": "inspect_or_recover_ace_assignment",
                    "tower_allowed_action": "nudge_leader_only",
                }
            )
        return blockers

    @staticmethod
    def _assignment_acceptance_state(assignment: Any) -> str:
        if getattr(assignment, "assignment_accepted", False):
            return "assignment_accepted"
        if getattr(assignment, "ace_reported_active", False):
            return "ace_reported_active"
        if getattr(assignment, "blocker_reason", None):
            return "blocked"
        dispatch_state = getattr(assignment, "dispatch_delivery_state", "queued_unverified")
        if dispatch_state == "accepted_active":
            return "awaiting_ace_active_report"
        if dispatch_state == "submitted_pending_acceptance":
            return "submitted_pending_acceptance"
        startup_state = getattr(
            assignment, "startup_readiness_state", "startup_handshake_pending"
        )
        if startup_state != "input_ready":
            return startup_state
        return dispatch_state

    async def monitor_ace_assignments(self, *, detailed: bool = False) -> list[dict[str, Any]]:
        """Leader-owned Ace monitoring summary.

        Tower should normally consume Leader/project health summaries and avoid
        direct Ace pane inspection. This method keeps Ace health inspection and
        blocked-dispatch reporting inside the Leader-owned task flow.
        """

        summaries: list[dict[str, Any]] = []
        for assignment in self.assignments.values():
            if assignment.status not in {"assigned", "working"}:
                continue
            health = await ace_health(self.conn, self.project_id, assignment.ace_session_id)
            data = health.as_dict()
            dispatch = data.get("ace_dispatch") or {}

            assignment.dispatch_delivery_state = dispatch.get(
                "dispatch_delivery_state",
                assignment.dispatch_delivery_state,
            )
            assignment.dispatch_verified = bool(
                dispatch.get("dispatch_verified", assignment.dispatch_verified)
            )
            assignment.blocker_reason = data.get("current_blocker") or dispatch.get(
                "blocker_reason"
            )
            assignment.last_activity_at = (
                data.get("last_activity_at") or assignment.last_activity_at
            )

            summary = {
                "task_graph_id": assignment.task_graph_id,
                "ace_session_id": assignment.ace_session_id,
                "status": assignment.status,
                "runtime_state": data.get("runtime_state"),
                "dispatch_delivery_state": assignment.dispatch_delivery_state,
                "dispatch_verified": assignment.dispatch_verified,
                "blocker_reason": assignment.blocker_reason,
                "last_activity_at": assignment.last_activity_at,
                "leader_owned": True,
            }
            if detailed:
                summary["health"] = data
            summaries.append(summary)

            if assignment.blocker_reason and self.event_bus:
                await self.event_bus.publish(
                    "leader_ace_blocked",
                    {
                        "leader_id": self.leader_id,
                        "project_id": self.project_id,
                        **summary,
                    },
                )

        return summaries

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

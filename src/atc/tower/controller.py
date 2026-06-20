"""Tower controller — manages goal intake and Leader lifecycle.

The Tower controller receives goals from the UI, builds context packages,
creates/starts Leader sessions, and monitors their progress. It publishes
status changes through the event bus so the WebSocket hub can relay them
to connected clients.

Tower lifecycle states: idle → planning → managing → complete | error

The full orchestration loop:
  User → Tower.submit_goal() → start_leader() → Leader running
  User → Tower.send_message() → send_leader_message() → Leader receives
  Leader PTY output → Tower._on_leader_output() → broadcast to UI
  Tower.get_progress() → explicit operator/status request only
  Leader completion hook → Tower.mark_complete() → idle
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
from typing import TYPE_CHECKING, Any

from atc.config import load_settings
from atc.leader.context_package import build_context_package
from atc.leader.kickoff import (
    build_leader_kickoff_message,
    persist_leader_kickoff_payload,
    verify_leader_kickoff_delivery,
)
from atc.leader.leader import send_leader_message, start_leader, stop_leader
from atc.runtime.health import leader_health
from atc.runtime.models import DeliveryState, RoleKind, RuntimeDeliveryResult, RuntimeState
from atc.session.state_machine import SessionStatus
from atc.state import db as db_ops
from atc.tower.monitoring import decide_tower_monitoring_cadence
from atc.tower.session import send_tower_message, start_tower_session, stop_tower_session

if TYPE_CHECKING:
    import aiosqlite

    from atc.api.ws.hub import WsHub
    from atc.core.events import EventBus

logger = logging.getLogger(__name__)


def _delivery_failed(result: object) -> bool:
    """Return true when a runtime delivery result is explicitly not ok."""

    return isinstance(result, RuntimeDeliveryResult) and not result.ok


class TowerState(enum.StrEnum):
    """Tower lifecycle states."""

    IDLE = "idle"
    PLANNING = "planning"
    MANAGING = "managing"
    COMPLETE = "complete"
    ERROR = "error"


# Valid state transitions for the tower lifecycle.
_VALID_TRANSITIONS: dict[TowerState, set[TowerState]] = {
    TowerState.IDLE: {TowerState.PLANNING},
    TowerState.PLANNING: {TowerState.MANAGING, TowerState.ERROR},
    TowerState.MANAGING: {TowerState.COMPLETE, TowerState.ERROR, TowerState.IDLE},
    TowerState.COMPLETE: {TowerState.IDLE},
    TowerState.ERROR: {TowerState.IDLE},
}


class TowerController:
    """Singleton controller that manages goal intake and Leader sessions.

    One TowerController per application instance. It tracks the current
    state of goal processing and coordinates with the Leader subsystem.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        event_bus: EventBus,
        ws_hub: WsHub | None = None,
        max_concurrent_aces: int | None = None,
    ) -> None:
        self._db = db
        self._event_bus = event_bus
        self._ws_hub = ws_hub
        self._state = TowerState.IDLE
        self._current_goal: str | None = None
        self._current_project_id: str | None = None
        # Tower's own Claude Code session (independent from Leader)
        self._current_session_id: str | None = None
        # Leader's session (separate terminal stream)
        self._leader_session_id: str | None = None

        # Track Leader output lines for monitoring
        self._leader_output_lines: list[str] = []
        self._max_output_lines = 200

        # Budget constraint flag — set when budget_warning fires, cleared on budget_ok
        self._budget_constrained = False

        # Concurrent session limit — counts active leader + ace sessions
        if max_concurrent_aces is None:
            try:
                max_concurrent_aces = load_settings().tower.max_concurrent_aces
            except Exception:
                max_concurrent_aces = 5
        self._max_concurrent_aces: int = max_concurrent_aces
        self._active_ace_count: int = 0

        # Subscribe to leader session events for startup/runtime safety and completion hooks.
        self._event_bus.subscribe("session_status_changed", self._on_session_status_changed)
        self._event_bus.subscribe("pty_output", self._on_agent_output)
        self._event_bus.subscribe("session_created", self._on_session_created)
        self._event_bus.subscribe("leader_project_completed", self._on_leader_project_completed)

        # Subscribe to budget events for proactive slowdown
        self._event_bus.subscribe("budget_warning", self._on_budget_warning)
        self._event_bus.subscribe("budget_ok", self._on_budget_ok)

    @property
    def state(self) -> TowerState:
        return self._state

    @property
    def current_goal(self) -> str | None:
        return self._current_goal

    @property
    def current_project_id(self) -> str | None:
        return self._current_project_id

    @property
    def current_session_id(self) -> str | None:
        return self._current_session_id

    async def _transition(self, target: TowerState) -> None:
        """Validate and perform a tower state transition."""
        allowed = _VALID_TRANSITIONS.get(self._state, set())
        if target not in allowed:
            raise InvalidTowerTransitionError(self._state, target)

        previous = self._state
        self._state = target
        logger.info("Tower: %s → %s", previous.value, target.value)

        await self._event_bus.publish(
            "tower_state_changed",
            {
                "previous_state": previous.value,
                "new_state": target.value,
                "project_id": self._current_project_id,
                "goal": self._current_goal,
            },
        )

        # Broadcast to WebSocket clients on the "tower" channel
        if self._ws_hub is not None:
            await self._ws_hub.broadcast(
                "tower",
                {
                    "type": "state_changed",
                    "previous_state": previous.value,
                    "new_state": target.value,
                    "project_id": self._current_project_id,
                    "goal": self._current_goal,
                },
            )

    async def start_session(self, project_id: str | None = None) -> str:
        """Start Tower's own Claude Code session (independent from Leader).

        Returns the tower session id.  If a session already exists, returns
        its id without spawning a new one.
        """
        requested_project_id = project_id
        self._current_project_id = requested_project_id

        session_id = await start_tower_session(
            self._db,
            requested_project_id,
            event_bus=self._event_bus,
        )
        self._current_session_id = session_id

        session = await db_ops.get_session(self._db, session_id)
        actual_project_id = session.project_id if session is not None else requested_project_id
        self._current_project_id = actual_project_id

        # Transition to MANAGING so the frontend shows the terminal
        if self._state in (TowerState.IDLE, TowerState.COMPLETE, TowerState.ERROR):
            if self._state in (TowerState.COMPLETE, TowerState.ERROR):
                self._state = TowerState.IDLE
            await self._transition(TowerState.PLANNING)
            await self._transition(TowerState.MANAGING)

        session = await db_ops.get_session(self._db, session_id)

        # Broadcast tower session info so the frontend subscribes immediately
        if self._ws_hub is not None:
            await self._ws_hub.broadcast(
                "tower",
                {
                    "type": "tower_session",
                    "session_id": session_id,
                    "status": "idle",
                    "project_id": actual_project_id,
                    "requested_project_id": requested_project_id,
                    "provider": session.provider if session else None,
                },
            )

        return session_id

    async def stop_session(self) -> None:
        """Stop Tower's own Claude Code session."""
        if self._current_session_id:
            await stop_tower_session(
                self._db,
                self._current_session_id,
                event_bus=self._event_bus,
            )

        # Also stop the Leader if running
        if self._current_project_id and self._leader_session_id:
            await stop_leader(
                self._db,
                self._current_project_id,
                event_bus=self._event_bus,
            )
            self._leader_session_id = None

        # Reset state
        if self._state in (TowerState.MANAGING, TowerState.PLANNING):
            await self._transition(TowerState.ERROR)

        if self._state in (TowerState.ERROR, TowerState.COMPLETE):
            self._state = TowerState.IDLE

        self._current_goal = None
        self._current_project_id = None
        self._current_session_id = None
        self._leader_output_lines.clear()

        await self._event_bus.publish(
            "tower_state_changed",
            {"previous_state": "managing", "new_state": "idle", "project_id": None, "goal": None},
        )

    async def submit_goal(self, project_id: str, goal: str) -> dict[str, Any]:
        """Process a new goal: build context, start Leader, begin monitoring.

        Returns a dict with status, project_id, session_id, and context_package.
        Raises if the tower is not idle (already processing a goal).
        """
        allowed = (TowerState.IDLE, TowerState.COMPLETE, TowerState.ERROR, TowerState.MANAGING)
        if self._state not in allowed:
            raise TowerBusyError(self._state, project_id)

        if self._budget_constrained:
            logger.warning("Skipping new Ace spawn — budget constrained (project %s)", project_id)
            raise BudgetConstrainedError(project_id)

        if self._active_ace_count >= self._max_concurrent_aces:
            logger.warning(
                "Skipping new session spawn — at capacity (active=%d, max=%d, project=%s)",
                self._active_ace_count,
                self._max_concurrent_aces,
                project_id,
            )
            raise TowerBusyError(self._state, project_id, detail="at capacity")

        # Reset to idle first if coming from complete/error
        if self._state in (TowerState.COMPLETE, TowerState.ERROR):
            self._state = TowerState.IDLE

        self._current_project_id = project_id
        self._current_goal = goal

        try:
            # If not already managing (i.e. Tower session not yet started)
            if self._state == TowerState.IDLE:
                await self._transition(TowerState.PLANNING)
                await self._transition(TowerState.MANAGING)

            # Build the context package
            context_package = await build_context_package(self._db, project_id, goal)

            # Store context on the leader row
            await self._db.execute(
                "UPDATE leaders SET context = ?, goal = ?, updated_at = datetime('now') "
                "WHERE project_id = ?",
                (json.dumps(context_package), goal, project_id),
            )
            await self._db.commit()

            # Seed project metadata as context entries so the context panel is
            # always populated, even on a fresh project with no prior context.
            seed_entries = [
                ("goal", "text", goal),
            ]
            if context_package.get("description"):
                seed_entries.append(("project_description", "text", context_package["description"]))
            if context_package.get("repo_path"):
                seed_entries.append(("repo_path", "text", context_package["repo_path"]))
            if context_package.get("github_repo"):
                seed_entries.append(("github_repo", "text", context_package["github_repo"]))

            for key, entry_type, value in seed_entries:
                try:
                    await db_ops.create_context_entry(
                        self._db,
                        scope="project",
                        key=key,
                        entry_type=entry_type,
                        value=str(value),
                        project_id=project_id,
                        updated_by="tower",
                    )
                    await self._db.commit()
                except Exception:
                    # Update existing entry if key already exists
                    try:
                        await self._db.execute(
                            "UPDATE context_entries SET value = ?, updated_at = datetime('now')"
                            " WHERE project_id = ? AND key = ? AND scope = 'project'",
                            (str(value), project_id, key),
                        )
                        await self._db.commit()
                    except Exception:
                        logger.debug("Could not seed context entry key=%r", key)

            # Persist any additional context_entries from the context package
            for entry in context_package.get("context_entries", []):
                try:
                    await db_ops.create_context_entry(
                        self._db,
                        scope="project",
                        key=entry.get("key", ""),
                        entry_type=entry.get("entry_type", "text"),
                        value=str(entry.get("value", "")),
                        project_id=project_id,
                        updated_by="tower",
                    )
                    await self._db.commit()
                except Exception:
                    logger.debug(
                        "Context entry key=%r already exists for project %s — skipping",
                        entry.get("key"),
                        project_id,
                    )

            # Start the Leader session (separate from Tower's session)
            leader_session_id = await start_leader(
                self._db,
                project_id,
                goal=goal,
                event_bus=self._event_bus,
                context_package=context_package,
            )
            self._leader_session_id = leader_session_id

            # Broadcast leader session info so LeaderConsole can subscribe
            if self._ws_hub is not None:
                await self._ws_hub.broadcast(
                    "tower",
                    {
                        "type": "leader_status",
                        "session_id": leader_session_id,
                        "status": "idle",
                        "project_id": project_id,
                    },
                )

            await self._event_bus.publish(
                "tower_goal_submitted",
                {
                    "project_id": project_id,
                    "goal": goal,
                    "session_id": leader_session_id,
                },
            )

            # Send kickoff and derive a provider-neutral startup verification verdict.
            kickoff_delivery = await self._send_leader_kickoff(leader_session_id, goal)
            verification = verify_leader_kickoff_delivery(kickoff_delivery)
            kickoff_payload_persisted = await self._leader_kickoff_payload_persisted(project_id)
            if verification.kickoff_verified:
                asyncio.create_task(
                    self._verify_leader_started(project_id, leader_session_id, goal)
                )

            # Notify Tower's own Claude session only after the kickoff has verified
            # enough runtime truth for normal monitoring. Blocked/unverified startup
            # stays surfaced as startup state instead of triggering a monitoring loop.
            if self._current_session_id and verification.kickoff_verified:
                asyncio.create_task(
                    self._notify_tower_goal_started(project_id, leader_session_id, goal)
                )

            if _delivery_failed(kickoff_delivery):
                return {
                    "status": kickoff_delivery.status,
                    "delivery_state": kickoff_delivery.status,
                    "message": kickoff_delivery.message
                    or f"Leader kickoff {kickoff_delivery.status}",
                    "project_id": project_id,
                    "session_id": self._current_session_id,
                    "leader_session_id": leader_session_id,
                    "context_package": context_package,
                    "provider": kickoff_delivery.provider_name,
                    "delivery": kickoff_delivery.as_dict(),
                    **verification.as_dict(),
                    "kickoff_payload_persisted": kickoff_payload_persisted,
                    "recovery": (
                        "inspect Leader runtime/session status before assuming "
                        "kickoff delivery"
                    ),
                }

            return {
                "status": "queued",
                "delivery_state": verification.kickoff_state,
                **verification.as_dict(),
                "kickoff_payload_persisted": kickoff_payload_persisted,
                "message": (
                    "Goal queued; Leader session was created and kickoff verification "
                    "is still pending"
                ),
                "project_id": project_id,
                "session_id": self._current_session_id,
                "leader_session_id": leader_session_id,
                "context_package": context_package,
                "recovery": (
                    "watch Tower/Leader status and runtime traces; queued is not proof "
                    "the Leader acted on the goal"
                ),
            }

        except Exception:
            logger.exception("Tower failed to process goal for project %s", project_id)
            await self._transition(TowerState.ERROR)
            raise

    async def mark_complete(self) -> None:
        """Mark the current goal as complete and return tower to idle."""
        await self._transition(TowerState.COMPLETE)
        self._current_goal = None
        self._leader_session_id = None
        self._leader_output_lines.clear()

    async def on_leader_project_completed(
        self,
        *,
        project_id: str,
        leader_id: str | None = None,
        session_id: str | None = None,
        summary: str | None = None,
        evidence: list[str] | None = None,
        reported_at: str | None = None,
    ) -> None:
        """Handle the Leader→Tower completion hook.

        This is the event-driven handoff path: after Leader accepts and owns the
        project, Tower should not poll the task graph for normal completion.
        Leader reports completion explicitly, and Tower marks its controller
        state complete plus notifies Tower's operator-facing session/UI.
        """
        if self._current_project_id and self._current_project_id != project_id:
            logger.info(
                "Ignoring completion report for project %s while Tower tracks %s",
                project_id,
                self._current_project_id,
            )
            return

        if self._state == TowerState.MANAGING:
            await self._transition(TowerState.COMPLETE)
        elif self._state not in (TowerState.COMPLETE, TowerState.IDLE):
            logger.info(
                "Leader completion report received while Tower state=%s", self._state.value
            )

        self._current_goal = None
        self._leader_session_id = None
        self._leader_output_lines.clear()

        payload = {
            "type": "leader_project_completed",
            "project_id": project_id,
            "leader_id": leader_id,
            "session_id": session_id,
            "summary": summary,
            "evidence": evidence or [],
            "reported_at": reported_at,
        }
        if self._ws_hub is not None:
            await self._ws_hub.broadcast("tower", payload)

        if self._current_session_id:
            evidence_lines = "\n".join(f"- {item}" for item in (evidence or []))
            notification = (
                f"[ATC] Leader reported project complete.\n"
                f"Project ID: {project_id}\n"
                f"Leader session: {session_id or 'unknown'}\n"
                f"Reported at: {reported_at or 'unknown'}\n"
                f"Summary: {summary or 'No summary provided.'}"
            )
            if evidence_lines:
                notification += f"\nEvidence:\n{evidence_lines}"
            try:
                await send_tower_message(
                    self._db,
                    self._current_session_id,
                    notification,
                    event_bus=self._event_bus,
                )
            except Exception as exc:
                logger.warning(
                    "Could not notify Tower session %s of Leader completion: %s",
                    self._current_session_id,
                    exc,
                )

    async def _on_leader_project_completed(self, data: dict[str, Any]) -> None:
        await self.on_leader_project_completed(
            project_id=str(data.get("project_id") or ""),
            leader_id=data.get("leader_id"),
            session_id=data.get("session_id"),
            summary=data.get("summary"),
            evidence=data.get("evidence") or [],
            reported_at=data.get("reported_at"),
        )

    async def cancel_goal(self) -> None:
        """Cancel the current goal and stop the Leader.

        If Tower has its own session, it stays in MANAGING. Otherwise
        transitions back to idle.
        """
        if self._current_project_id and self._leader_session_id:
            await stop_leader(
                self._db,
                self._current_project_id,
                event_bus=self._event_bus,
            )
            self._leader_session_id = None

        self._current_goal = None
        self._leader_output_lines.clear()

        # If Tower has no session of its own, return to idle
        if not self._current_session_id:
            if self._state == TowerState.MANAGING:
                await self._transition(TowerState.ERROR)
            if self._state in (TowerState.ERROR, TowerState.COMPLETE):
                self._state = TowerState.IDLE
                self._current_project_id = None
                await self._event_bus.publish(
                    "tower_state_changed",
                    {
                        "previous_state": "error",
                        "new_state": "idle",
                        "project_id": None,
                        "goal": None,
                    },
                )

    async def reset(self) -> None:
        """Force-reset tower to idle state (e.g. after unrecoverable error)."""
        self._state = TowerState.IDLE
        self._current_goal = None
        self._current_project_id = None
        self._current_session_id = None
        self._leader_session_id = None
        self._leader_output_lines.clear()

    def get_status(self) -> dict[str, Any]:
        """Return the current tower controller status."""
        return {
            "state": self._state.value,
            "current_goal": self._current_goal,
            "current_project_id": self._current_project_id,
            "current_session_id": self._current_session_id,
            "leader_session_id": self._leader_session_id,
            "output_line_count": len(self._leader_output_lines),
            "active_ace_count": self._active_ace_count,
            "max_aces": self._max_concurrent_aces,
        }

    async def send_message(self, message: str) -> None:
        """Send a message to Tower's own terminal.

        This types the message into Tower's Claude Code session.

        Raises ``ValueError`` if no Tower session is active.
        """
        if not self._current_session_id:
            raise ValueError("No active Tower session")

        await send_tower_message(
            self._db,
            self._current_session_id,
            message,
            event_bus=self._event_bus,
        )

        logger.info("Sent message to Tower session (project %s)", self._current_project_id)

        if self._ws_hub is not None:
            await self._ws_hub.broadcast(
                "tower",
                {
                    "type": "message_sent",
                    "project_id": self._current_project_id,
                    "message": message,
                },
            )

    async def get_progress(self) -> dict[str, Any]:
        """Query the current Leader's task graph progress.

        The legacy counters remain task-lifecycle counts. Runtime and delivery
        truth are surfaced separately so callers do not infer active Ace work
        from assignment intent alone.
        """
        empty = {
            "project_id": self._current_project_id,
            "total": 0,
            "done": 0,
            "in_progress": 0,
            "todo": 0,
            "progress_pct": 0,
            "all_done": False,
            "task_states": {},
            "runtime_states": {},
            "delivery_states": {},
            "blocked": 0,
            "dispatch_unverified": 0,
        }
        if not self._current_project_id or self._state != TowerState.MANAGING:
            return empty

        task_graphs = await db_ops.list_task_graphs(
            self._db, project_id=self._current_project_id
        )
        assignments = await db_ops.list_task_assignments(self._db)
        active_assignments = {
            assignment.task_graph_id: assignment
            for assignment in assignments
            if assignment.status in {"assigned", "working"}
        }

        task_states: dict[str, int] = {}
        runtime_states: dict[str, int] = {}
        delivery_states: dict[str, int] = {}
        blocked = 0
        dispatch_unverified = 0

        for task_graph in task_graphs:
            task_states[task_graph.status] = task_states.get(task_graph.status, 0) + 1
            assignment = active_assignments.get(task_graph.id)
            delivery_state = (
                assignment.dispatch_delivery_state
                if assignment is not None
                else DeliveryState.NOT_STARTED.value
            )
            delivery_states[delivery_state] = delivery_states.get(delivery_state, 0) + 1

            dispatch_verified = bool(assignment.dispatch_verified) if assignment else False
            blocker_reason = assignment.blocker_reason if assignment else None
            if blocker_reason or delivery_state == DeliveryState.BLOCKED.value:
                runtime_state = RuntimeState.BLOCKED.value
                blocked += 1
            elif task_graph.status == "done":
                runtime_state = RuntimeState.COMPLETE.value
            elif delivery_state == DeliveryState.ACCEPTED_ACTIVE.value and dispatch_verified:
                runtime_state = RuntimeState.ACTIVE.value
            elif delivery_state in {
                DeliveryState.QUEUED_UNVERIFIED.value,
                DeliveryState.RUNTIME_CREATED.value,
                DeliveryState.PROMPT_VISIBLE.value,
                DeliveryState.PAYLOAD_WRITTEN.value,
                DeliveryState.SUBMIT_SENT.value,
                DeliveryState.SUBMITTED_PENDING_ACCEPTANCE.value,
            }:
                runtime_state = RuntimeState.STARTING.value
                if not dispatch_verified:
                    dispatch_unverified += 1
            elif delivery_state == DeliveryState.FAILED.value:
                runtime_state = RuntimeState.FAILED.value
            else:
                runtime_state = RuntimeState.IDLE.value
            runtime_states[runtime_state] = runtime_states.get(runtime_state, 0) + 1

        total = len(task_graphs)
        done = task_states.get("done", 0)
        in_progress = task_states.get("in_progress", 0)
        todo = task_states.get("todo", 0)
        progress_pct = int((done / total) * 100) if total > 0 else 0
        all_done = total > 0 and done == total

        progress = {
            "project_id": self._current_project_id,
            "total": total,
            "done": done,
            "in_progress": in_progress,
            "todo": todo,
            "progress_pct": progress_pct,
            "all_done": all_done,
            "task_states": task_states,
            "runtime_states": runtime_states,
            "delivery_states": delivery_states,
            "blocked": blocked,
            "dispatch_unverified": dispatch_unverified,
        }

        # Broadcast progress to frontend
        if self._ws_hub is not None:
            await self._ws_hub.broadcast(
                "tower",
                {"type": "progress", **progress},
            )

        return progress

    async def _respawn_leader(self, project_id: str, goal: str) -> str | None:
        """Respawn a dead leader and return the new session_id, or None on failure."""
        try:
            logger.info("Respawning dead leader for project %s", project_id)
            # Wipe the dead session link from the leader row so start_leader
            # creates a fresh session instead of reusing the corpse.
            await self._db.execute(
                "UPDATE leaders SET session_id = NULL, status = 'idle',"
                " updated_at = datetime('now') WHERE project_id = ?",
                (project_id,),
            )
            await self._db.commit()

            context_package = await build_context_package(self._db, project_id, goal)
            new_session_id = await start_leader(
                self._db,
                project_id,
                goal=goal,
                event_bus=self._event_bus,
                context_package=context_package,
            )
            self._leader_session_id = new_session_id
            self._leader_output_lines.clear()
            logger.info("Leader respawned: new session %s", new_session_id)
            return new_session_id
        except Exception:
            logger.exception("Failed to respawn leader for project %s", project_id)
            return None

    async def _leader_kickoff_payload_persisted(self, project_id: str) -> bool:
        leader = await db_ops.get_leader_by_project(self._db, project_id)
        if leader is None or not leader.context:
            return False
        if isinstance(leader.context, dict):
            context = leader.context
        else:
            try:
                context = json.loads(leader.context)
            except json.JSONDecodeError:
                return False
        return isinstance(context, dict) and bool(context.get("leader_kickoff_payload"))

    async def _send_leader_kickoff(
        self, session_id: str, goal: str
    ) -> RuntimeDeliveryResult | None:
        """Send the initial kickoff message to the Leader pane, including context."""
        if not self._current_project_id:
            return None
        try:
            # Fetch project metadata and context entries for a rich kickoff prompt
            cursor = await self._db.execute(
                "SELECT name, description, repo_path, github_repo FROM projects WHERE id = ?",
                (self._current_project_id,),
            )
            row = await cursor.fetchone()
            project_name = row[0] if row else "Unknown"
            description = row[1] if row else None
            repo_path = row[2] if row else None
            github_repo = row[3] if row else None

            cursor = await self._db.execute(
                "SELECT key, value FROM context_entries "
                "WHERE project_id = ? AND scope = 'project' "
                "ORDER BY created_at ASC",
                (self._current_project_id,),
            )
            context_rows = await cursor.fetchall()

            kickoff_msg = build_leader_kickoff_message(
                project_id=self._current_project_id,
                project_name=project_name,
                goal=goal,
                description=description,
                repo_path=repo_path,
                github_repo=github_repo,
                context_rows=context_rows,
            )
            await persist_leader_kickoff_payload(
                self._db,
                project_id=self._current_project_id,
                goal=goal,
                message=kickoff_msg,
                source="tower-submit-goal",
                auto_kickoff=True,
            )

            try:
                delivery = await send_leader_message(
                    self._db,
                    self._current_project_id,
                    kickoff_msg,
                    event_bus=self._event_bus,
                )
                if _delivery_failed(delivery):
                    return delivery
            except ValueError as exc:
                # Pane died between spawn and kickoff — respawn once and retry
                err_msg = str(exc).lower()
                if "dead" in err_msg or "error" in err_msg or "disconnected" in err_msg:
                    logger.warning(
                        "Leader pane dead during kickoff for session %s — respawning once",
                        session_id,
                    )
                    new_id = await self._respawn_leader(self._current_project_id, goal)
                    if new_id:
                        # Retry kickoff with fresh session
                        retry_delivery = await send_leader_message(
                            self._db,
                            self._current_project_id,
                            kickoff_msg,
                            event_bus=self._event_bus,
                        )
                        if _delivery_failed(retry_delivery):
                            return retry_delivery
                        session_id = new_id
                        delivery = retry_delivery
                    else:
                        raise
                else:
                    raise
            logger.info(
                "Sent kickoff message to leader session %s (project %s)",
                session_id,
                self._current_project_id,
            )
            return delivery
        except Exception as exc:
            logger.exception("Failed to send kickoff to leader session %s", session_id)
            return RuntimeDeliveryResult(
                session_id=session_id,
                provider_name="unknown",
                role=RoleKind.LEADER,
                status="failed",
                stage="kickoff",
                verdict="failed",
                reason_code="kickoff_failed",
                message=str(exc),
            )

    async def _verify_leader_started(self, project_id: str, session_id: str, goal: str) -> None:
        """Background loop: verify the Leader acknowledged and started working.

        Tower performs active startup/kickoff verification for roughly two
        minutes. Once provider-neutral Leader health shows recent activity or
        task creation, Tower backs off and leaves Ace monitoring/recovery to the
        Leader-owned task flow.
        """
        startup_elapsed = 0
        await asyncio.sleep(10)
        startup_elapsed += 10

        while startup_elapsed <= 120:
            if self._leader_session_id != session_id:
                # Leader was replaced or cancelled — stop verifying
                return

            if self._leader_output_lines:
                logger.info(
                    "Leader session %s is producing output after kickoff; Tower backing off",
                    session_id,
                )
                return

            try:
                health = await leader_health(self._db, project_id)
                decision = decide_tower_monitoring_cadence(
                    health.as_dict(),
                    startup_elapsed_seconds=startup_elapsed,
                )
            except Exception:
                logger.exception("Could not inspect Leader health for project %s", project_id)
                decision = decide_tower_monitoring_cadence(
                    {
                        "runtime_exists": True,
                        "pane_attached": True,
                        "runtime_state": "starting",
                    },
                    startup_elapsed_seconds=startup_elapsed,
                )

            if (
                decision.mode in {"leader_backoff", "leader_health"}
                and not decision.should_nudge_leader
            ):
                logger.info(
                    "Leader session %s startup verified via runtime health (%s); "
                    "Tower backing off to Leader/project monitoring cadence",
                    session_id,
                    decision.reason,
                )
                return

            if decision.inspect_aces:
                logger.warning(
                    "Leader session %s requires detailed inspection/recovery (%s)",
                    session_id,
                    decision.reason,
                )
                if self._state == TowerState.MANAGING:
                    await self._transition(TowerState.ERROR)
                return

            if not decision.should_nudge_leader:
                await asyncio.sleep(decision.next_poll_seconds)
                startup_elapsed += decision.next_poll_seconds
                continue

            logger.warning(
                "Leader session %s startup not yet verified after %ds (%s) — sending nudge",
                session_id,
                startup_elapsed,
                decision.reason,
            )
            try:
                delivery = await send_leader_message(
                    self._db,
                    project_id,
                    "Please continue with your goal.",
                    event_bus=self._event_bus,
                )
                if _delivery_failed(delivery):
                    raise ValueError(
                        delivery.message
                        or f"Leader nudge {delivery.status}: {delivery.reason_code}"
                    )
            except ValueError as exc:
                # Pane is dead — respawn the leader and resend the kickoff
                err_msg = str(exc).lower()
                if "dead" in err_msg or "error" in err_msg or "disconnected" in err_msg:
                    logger.warning(
                        "Leader pane dead for session %s during startup verification — respawning",
                        session_id,
                    )
                    new_id = await self._respawn_leader(project_id, goal)
                    if new_id:
                        session_id = new_id
                        await self._send_leader_kickoff(new_id, goal)
                        # Give new leader time to produce output before next check.
                        await asyncio.sleep(30)
                        startup_elapsed += 30
                        continue
                    else:
                        logger.error("Leader respawn failed — giving up")
                        break
                logger.exception("Failed to send nudge to leader session %s", session_id)
            except Exception:
                logger.exception("Failed to send nudge to leader session %s", session_id)
            await asyncio.sleep(decision.next_poll_seconds)
            startup_elapsed += decision.next_poll_seconds

        # Final check after last retry
        if self._leader_session_id != session_id:
            return

        if not self._leader_output_lines:
            logger.error(
                "Leader session %s did not verify kickoff within startup window — "
                "setting tower state to ERROR",
                session_id,
            )
            if self._state == TowerState.MANAGING:
                await self._transition(TowerState.ERROR)

    async def _notify_tower_goal_started(
        self, project_id: str, leader_session_id: str, goal: str
    ) -> None:
        """Send a brief notification to Tower's own Claude terminal.

        After submit_goal() starts a Leader, Tower's Claude session needs to
        know the goal and leader session ID so it can begin monitoring.
        This is fire-and-forget: if Tower's terminal isn't ready, we log and move on.
        """
        if not self._current_session_id:
            return

        # Wait briefly for Tower's pane to be ready before sending
        await asyncio.sleep(3.0)

        # Look up the project name for a friendlier message
        try:
            cursor = await self._db.execute(
                "SELECT name FROM projects WHERE id = ?",
                (project_id,),
            )
            row = await cursor.fetchone()
            project_name = row[0] if row else project_id[:8]
        except Exception:
            project_name = project_id[:8]

        notification = (
            f"[ATC] Leader started for project '{project_name}'.\n"
            f"Goal: {goal}\n"
            f"Leader session: {leader_session_id}\n"
            f"Project ID: {project_id}\n\n"
            "Startup/goal acceptance has enough provider-neutral proof for handoff. "
            "The Leader is now the busy project session and owns task graph/Ace monitoring.\n"
            "Do NOT poll normal project progress to discover completion. Wait for the "
            "Leader completion hook instead:\n"
            f"  atc leader report-complete --project-id {project_id} --summary '...'\n"
            "Only inspect Leader health if the Leader reports blocked/failed, the runtime "
            "disappears, the operator asks for detail, or an explicit recovery threshold "
            "is reached. "
            "Do NOT write code or create files yourself — delegate to the Leader only."
        )

        try:
            await send_tower_message(
                self._db,
                self._current_session_id,
                notification,
                event_bus=self._event_bus,
            )
            logger.info(
                "Sent goal notification to Tower session %s (project %s)",
                self._current_session_id,
                project_id,
            )
        except Exception as exc:
            logger.warning(
                "Could not notify Tower session %s of new goal: %s",
                self._current_session_id,
                exc,
            )

    @staticmethod
    def _extract_auth_blocker(text: str) -> str | None:
        """Return a human-readable auth blocker when Claude is not usable."""
        lowered = text.lower()
        if "not logged in" in lowered and "/login" in lowered:
            return "Claude Code is not logged in on the host. Run /login in the affected pane."
        if "run in another terminal: security unlock-keychain" in lowered:
            return "Claude Code cannot access macOS keychain; unlock the keychain on the host."
        return None

    async def _mark_session_error(self, session_id: str, reason: str) -> None:
        session = await db_ops.get_session(self._db, session_id)
        if session is None or session.status == SessionStatus.ERROR.value:
            return

        await db_ops.update_session_status(self._db, session_id, SessionStatus.ERROR.value)
        await self._event_bus.publish(
            "session_status_changed",
            {
                "session_id": session_id,
                "previous_status": session.status,
                "new_status": SessionStatus.ERROR.value,
                "reason": reason,
            },
        )

    async def _handle_auth_blocked_session(self, session_id: str, reason: str) -> None:
        await self._mark_session_error(session_id, reason)

        if self._ws_hub is not None and session_id == self._leader_session_id:
            await self._ws_hub.broadcast(
                "tower",
                {
                    "type": "leader_activity",
                    "session_id": session_id,
                    "preview": reason,
                },
            )

        if session_id in {self._leader_session_id, self._current_session_id}:
            logger.error("Tower session %s blocked by auth issue: %s", session_id, reason)
            if self._state in (TowerState.PLANNING, TowerState.MANAGING):
                await self._transition(TowerState.ERROR)

    async def _on_agent_output(self, data: dict[str, Any]) -> None:
        """Capture PTY output from current Tower/Leader sessions for monitoring."""
        session_id = data.get("session_id")
        if session_id not in {self._leader_session_id, self._current_session_id}:
            return

        raw = data.get("data", b"")
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)

        reason = self._extract_auth_blocker(text)
        if reason is not None:
            await self._handle_auth_blocked_session(session_id, reason)

        if session_id != self._leader_session_id:
            return

        lines = text.splitlines()
        self._leader_output_lines.extend(lines)
        if len(self._leader_output_lines) > self._max_output_lines:
            self._leader_output_lines = self._leader_output_lines[-self._max_output_lines :]

        if self._ws_hub is not None:
            stripped = text.strip()
            if stripped:
                await self._ws_hub.broadcast(
                    "tower",
                    {
                        "type": "leader_activity",
                        "session_id": session_id,
                        "preview": stripped[:200],
                    },
                )

    async def _on_session_created(self, data: dict[str, Any]) -> None:
        """Increment active ace counter when a leader or ace session is created."""
        session_type = data.get("session_type", "")
        if session_type in ("leader", "ace"):
            self._active_ace_count += 1
            logger.debug(
                "Session created (type=%s) — active_ace_count=%d/%d",
                session_type,
                self._active_ace_count,
                self._max_concurrent_aces,
            )

    async def _on_budget_warning(self, data: dict[str, Any]) -> None:
        """Handle budget_warning event — pause new Ace spawns."""
        project_id = data.get("project_id", "unknown")
        self._budget_constrained = True
        logger.warning(
            "Budget at warn threshold for project %s — pausing new Ace spawns", project_id
        )

    async def _on_budget_ok(self, data: dict[str, Any]) -> None:
        """Handle budget_ok event — resume new Ace spawns."""
        self._budget_constrained = False
        logger.info("Budget back below threshold — resuming Ace spawns")

    async def _on_session_status_changed(self, data: dict[str, Any]) -> None:
        """Monitor Leader session status changes for error detection and counter updates."""
        session_id = data.get("session_id")
        new_status = data.get("new_status")

        # Decrement active counter when any leader/ace session reaches a terminal status
        terminal_statuses = {"disconnected", "error", "completed", "cancelled"}
        if new_status in terminal_statuses:
            # Look up session type to decide if this counts against our limit
            try:
                cursor = await self._db.execute(
                    "SELECT session_type FROM sessions WHERE id = ?", (session_id,)
                )
                row = await cursor.fetchone()
                if row and row[0] in ("leader", "ace"):
                    self._active_ace_count = max(0, self._active_ace_count - 1)
                    logger.debug(
                        "Session %s terminal (status=%s) — active_ace_count=%d/%d",
                        session_id,
                        new_status,
                        self._active_ace_count,
                        self._max_concurrent_aces,
                    )
            except Exception:
                logger.debug(
                    "Could not look up session type for %s during status change", session_id
                )

        if session_id != self._leader_session_id:
            return

        if new_status == "error" and self._state == TowerState.MANAGING:
            logger.warning(
                "Leader session %s entered error state — transitioning tower to error",
                session_id,
            )
            await self._transition(TowerState.ERROR)

        # Broadcast leader status through the tower channel too
        if self._ws_hub is not None:
            await self._ws_hub.broadcast(
                "tower",
                {
                    "type": "leader_status",
                    "session_id": session_id,
                    "status": new_status,
                    "project_id": self._current_project_id,
                },
            )


class InvalidTowerTransitionError(Exception):
    """Raised when a tower state transition is not allowed."""

    def __init__(self, current: TowerState, target: TowerState) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid tower transition: {current.value} → {target.value}")


class TowerBusyError(Exception):
    """Raised when a goal is submitted while the tower is busy."""

    def __init__(self, state: TowerState, project_id: str, detail: str | None = None) -> None:
        self.state = state
        self.project_id = project_id
        self.detail = detail
        msg = f"Tower is busy (state={state.value}), cannot accept goal for {project_id}"
        if detail:
            msg = f"{msg}: {detail}"
        super().__init__(msg)


class BudgetConstrainedError(Exception):
    """Raised when a new Ace spawn is blocked due to budget warning threshold."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(f"Budget constrained — new Ace spawns paused for project {project_id}")

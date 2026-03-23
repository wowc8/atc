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
  Tower.get_progress() → Leader task graph status → broadcast to UI
  Leader all done → Tower.mark_complete() → idle
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
from typing import TYPE_CHECKING, Any

from atc.leader.context_package import build_context_package
from atc.leader.leader import send_leader_message, start_leader, stop_leader
from atc.state import db as db_ops
from atc.tower.session import send_tower_message, start_tower_session, stop_tower_session

if TYPE_CHECKING:
    import aiosqlite

    from atc.api.ws.hub import WsHub
    from atc.core.events import EventBus

logger = logging.getLogger(__name__)


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

        # Subscribe to leader session events for monitoring
        self._event_bus.subscribe("session_status_changed", self._on_session_status_changed)
        self._event_bus.subscribe("pty_output", self._on_leader_output)

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
        self._current_project_id = project_id

        session_id = await start_tower_session(
            self._db,
            project_id,
            event_bus=self._event_bus,
        )
        self._current_session_id = session_id

        # Transition to MANAGING so the frontend shows the terminal
        if self._state in (TowerState.IDLE, TowerState.COMPLETE, TowerState.ERROR):
            if self._state in (TowerState.COMPLETE, TowerState.ERROR):
                self._state = TowerState.IDLE
            await self._transition(TowerState.PLANNING)
            await self._transition(TowerState.MANAGING)

        # Broadcast tower session info so the frontend subscribes immediately
        if self._ws_hub is not None:
            await self._ws_hub.broadcast(
                "tower",
                {
                    "type": "tower_session",
                    "session_id": session_id,
                    "status": "idle",
                    "project_id": project_id,
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
            logger.warning(
                "Skipping new Ace spawn — budget constrained (project %s)", project_id
            )
            raise BudgetConstrainedError(project_id)

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

            # Send kickoff and start background verification loop
            await self._send_leader_kickoff(leader_session_id, goal)
            asyncio.create_task(
                self._verify_leader_started(project_id, leader_session_id, goal)
            )

            return {
                "status": "accepted",
                "project_id": project_id,
                "session_id": self._current_session_id,
                "leader_session_id": leader_session_id,
                "context_package": context_package,
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

        Returns a dict with task counts and completion percentage.
        If no goal is active, returns an empty progress summary.
        """
        if not self._current_project_id or self._state != TowerState.MANAGING:
            return {
                "project_id": self._current_project_id,
                "total": 0,
                "done": 0,
                "in_progress": 0,
                "todo": 0,
                "progress_pct": 0,
                "all_done": False,
            }

        cursor = await self._db.execute(
            "SELECT status, COUNT(*) as cnt FROM task_graphs "
            "WHERE project_id = ? GROUP BY status",
            (self._current_project_id,),
        )
        rows = await cursor.fetchall()

        counts: dict[str, int] = {}
        total = 0
        for row in rows:
            counts[row[0]] = row[1]
            total += row[1]

        done = counts.get("done", 0)
        in_progress = counts.get("in_progress", 0)
        todo = counts.get("todo", 0)
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
        }

        # Broadcast progress to frontend
        if self._ws_hub is not None:
            await self._ws_hub.broadcast(
                "tower",
                {"type": "progress", **progress},
            )

        return progress

    async def _send_leader_kickoff(self, session_id: str, goal: str) -> None:
        """Send the initial kickoff message to the Leader pane."""
        if not self._current_project_id:
            return
        try:
            kickoff_msg = (
                f"Your goal is: {goal}\n\n"
                "Begin immediately: decompose this goal into tasks, spawn Aces, and drive to completion."
            )
            await send_leader_message(
                self._db,
                self._current_project_id,
                kickoff_msg,
                event_bus=self._event_bus,
            )
            logger.info(
                "Sent kickoff message to leader session %s (project %s)",
                session_id,
                self._current_project_id,
            )
        except Exception:
            logger.exception(
                "Failed to send kickoff to leader session %s", session_id
            )

    async def _verify_leader_started(
        self, project_id: str, session_id: str, goal: str
    ) -> None:
        """Background loop: verify the Leader acknowledged and started working.

        Waits ~10s after kickoff, checks for output, and retries the kickoff
        up to 3 times (30s window each) before giving up and setting ERROR.
        """
        max_retries = 3
        initial_wait = 10
        retry_wait = 30

        await asyncio.sleep(initial_wait)

        for attempt in range(max_retries):
            if self._leader_session_id != session_id:
                # Leader was replaced or cancelled — stop verifying
                return

            if self._leader_output_lines:
                logger.info(
                    "Leader session %s is producing output after kickoff (attempt %d)",
                    session_id,
                    attempt + 1,
                )
                return

            logger.warning(
                "Leader session %s has no output yet (attempt %d/%d) — resending kickoff",
                session_id,
                attempt + 1,
                max_retries,
            )
            await self._send_leader_kickoff(session_id, goal)
            await asyncio.sleep(retry_wait)

        # Final check after last retry
        if self._leader_session_id != session_id:
            return

        if not self._leader_output_lines:
            logger.error(
                "Leader session %s produced no output after %d kickoff attempts — "
                "setting tower state to ERROR",
                session_id,
                max_retries,
            )
            if self._state == TowerState.MANAGING:
                await self._transition(TowerState.ERROR)

    async def _on_leader_output(self, data: dict[str, Any]) -> None:
        """Capture PTY output from the Leader session for monitoring.

        Only captures output from the current Leader session. Stores
        recent lines for the Tower to inspect and broadcasts a summary
        event so the frontend can show activity indicators.
        """
        session_id = data.get("session_id")
        if session_id != self._leader_session_id:
            return

        raw = data.get("data", b"")
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)

        # Store output lines (ring buffer)
        lines = text.splitlines()
        self._leader_output_lines.extend(lines)
        if len(self._leader_output_lines) > self._max_output_lines:
            self._leader_output_lines = self._leader_output_lines[-self._max_output_lines :]

        # Broadcast a lightweight activity event for the Tower UI
        if self._ws_hub is not None:
            # Only broadcast non-empty, visible text
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
        """Monitor Leader session status changes for error detection."""
        session_id = data.get("session_id")
        new_status = data.get("new_status")

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

    def __init__(self, state: TowerState, project_id: str) -> None:
        self.state = state
        self.project_id = project_id
        super().__init__(
            f"Tower is busy (state={state.value}), cannot accept goal for {project_id}"
        )


class BudgetConstrainedError(Exception):
    """Raised when a new Ace spawn is blocked due to budget warning threshold."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(
            f"Budget constrained — new Ace spawns paused for project {project_id}"
        )

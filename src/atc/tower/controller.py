"""Tower controller — manages goal intake and Leader lifecycle.

The Tower controller receives goals from the UI, builds context packages,
creates/starts Leader sessions, and monitors their progress. It publishes
status changes through the event bus so the WebSocket hub can relay them
to connected clients.

Tower lifecycle states: idle → planning → managing → complete | error
"""

from __future__ import annotations

import enum
import json
import logging
from typing import TYPE_CHECKING, Any

from atc.leader.context_package import build_context_package
from atc.leader.leader import start_leader, stop_leader

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
        self._current_session_id: str | None = None

        # Subscribe to leader session events for monitoring
        self._event_bus.subscribe("session_status_changed", self._on_session_status_changed)

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

    async def submit_goal(self, project_id: str, goal: str) -> dict[str, Any]:
        """Process a new goal: build context, start Leader, begin monitoring.

        Returns a dict with status, project_id, session_id, and context_package.
        Raises if the tower is not idle (already processing a goal).
        """
        if self._state not in (TowerState.IDLE, TowerState.COMPLETE, TowerState.ERROR):
            raise TowerBusyError(self._state, project_id)

        # Reset to idle first if coming from complete/error
        if self._state in (TowerState.COMPLETE, TowerState.ERROR):
            self._state = TowerState.IDLE

        self._current_project_id = project_id
        self._current_goal = goal

        try:
            # Phase 1: Planning — build the context package
            await self._transition(TowerState.PLANNING)

            context_package = await build_context_package(self._db, project_id, goal)

            # Store context on the leader row
            await self._db.execute(
                "UPDATE leaders SET context = ?, goal = ?, updated_at = datetime('now') "
                "WHERE project_id = ?",
                (json.dumps(context_package), goal, project_id),
            )
            await self._db.commit()

            # Phase 2: Managing — start the Leader session
            await self._transition(TowerState.MANAGING)

            session_id = await start_leader(
                self._db,
                project_id,
                goal=goal,
                event_bus=self._event_bus,
            )
            self._current_session_id = session_id

            await self._event_bus.publish(
                "tower_goal_submitted",
                {
                    "project_id": project_id,
                    "goal": goal,
                    "session_id": session_id,
                },
            )

            return {
                "status": "accepted",
                "project_id": project_id,
                "session_id": session_id,
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
        self._current_project_id = None
        self._current_session_id = None

    async def cancel_goal(self) -> None:
        """Cancel the current goal, stop the Leader, and return to idle."""
        if self._current_project_id and self._state == TowerState.MANAGING:
            await stop_leader(
                self._db,
                self._current_project_id,
                event_bus=self._event_bus,
            )

        # Transition through error → idle for cancellation
        if self._state == TowerState.MANAGING:
            await self._transition(TowerState.ERROR)

        if self._state in (TowerState.ERROR, TowerState.COMPLETE):
            self._state = TowerState.IDLE
            self._current_goal = None
            self._current_project_id = None
            self._current_session_id = None

            await self._event_bus.publish(
                "tower_state_changed",
                {"previous_state": "error", "new_state": "idle", "project_id": None, "goal": None},
            )

    async def reset(self) -> None:
        """Force-reset tower to idle state (e.g. after unrecoverable error)."""
        self._state = TowerState.IDLE
        self._current_goal = None
        self._current_project_id = None
        self._current_session_id = None

    def get_status(self) -> dict[str, Any]:
        """Return the current tower controller status."""
        return {
            "state": self._state.value,
            "current_goal": self._current_goal,
            "current_project_id": self._current_project_id,
            "current_session_id": self._current_session_id,
        }

    async def _on_session_status_changed(self, data: dict[str, Any]) -> None:
        """Monitor Leader session status changes for error detection."""
        session_id = data.get("session_id")
        new_status = data.get("new_status")

        if session_id != self._current_session_id:
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

"""Session state machine — SessionStatus enum and valid transition map.

The state machine enforces that sessions can only move between allowed states.
Every status change MUST go through ``transition()`` which validates and
publishes a ``session_status_changed`` event on the event bus.
"""

from __future__ import annotations

import enum
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from atc.core.events import EventBus

logger = logging.getLogger(__name__)


class SessionStatus(enum.StrEnum):
    """All possible session states.

    Using ``str`` mixin so the value serialises cleanly to/from SQLite TEXT.
    """

    IDLE = "idle"
    CONNECTING = "connecting"
    WORKING = "working"
    PAUSED = "paused"
    WAITING = "waiting"
    DISCONNECTED = "disconnected"
    ERROR = "error"


# Map of current status → set of statuses it may move to.
VALID_TRANSITIONS: dict[SessionStatus, set[SessionStatus]] = {
    SessionStatus.IDLE: {
        SessionStatus.CONNECTING,
        SessionStatus.WORKING,
        SessionStatus.PAUSED,
        SessionStatus.ERROR,
    },
    SessionStatus.CONNECTING: {
        SessionStatus.IDLE,
        SessionStatus.WORKING,
        SessionStatus.ERROR,
        SessionStatus.DISCONNECTED,
    },
    SessionStatus.WORKING: {
        SessionStatus.IDLE,
        SessionStatus.WAITING,
        SessionStatus.PAUSED,
        SessionStatus.ERROR,
        SessionStatus.DISCONNECTED,
    },
    SessionStatus.PAUSED: {
        SessionStatus.IDLE,
        SessionStatus.WORKING,
        SessionStatus.ERROR,
    },
    SessionStatus.WAITING: {
        SessionStatus.WORKING,
        SessionStatus.IDLE,
        SessionStatus.PAUSED,
        SessionStatus.ERROR,
        SessionStatus.DISCONNECTED,
    },
    SessionStatus.DISCONNECTED: {
        SessionStatus.CONNECTING,
        SessionStatus.ERROR,
    },
    SessionStatus.ERROR: {
        SessionStatus.CONNECTING,
        SessionStatus.IDLE,
    },
}


def is_valid_transition(current: SessionStatus, target: SessionStatus) -> bool:
    """Return ``True`` if *current* → *target* is an allowed transition."""
    return target in VALID_TRANSITIONS.get(current, set())


class InvalidTransitionError(Exception):
    """Raised when a session transition is not allowed."""

    def __init__(self, session_id: str, current: SessionStatus, target: SessionStatus) -> None:
        self.session_id = session_id
        self.current = current
        self.target = target
        super().__init__(
            f"Invalid transition for session {session_id}: {current.value} → {target.value}"
        )


async def transition(
    session_id: str,
    current: SessionStatus,
    target: SessionStatus,
    event_bus: EventBus | None = None,
) -> None:
    """Validate and publish a session status transition.

    Raises :class:`InvalidTransitionError` if the transition is not allowed.
    Does **not** write to DB — the caller is responsible for persisting.
    """
    if not is_valid_transition(current, target):
        raise InvalidTransitionError(session_id, current, target)

    logger.info("Session %s: %s → %s", session_id, current.value, target.value)

    if event_bus is not None:
        await event_bus.publish(
            "session_status_changed",
            {
                "session_id": session_id,
                "previous_status": current.value,
                "new_status": target.value,
            },
        )

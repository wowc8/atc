"""Leader session lifecycle — start / stop / message.

A Leader is one per project.  It uses the same session infrastructure as aces
but has session_type ``manager``.  The Leader row in the ``leaders`` table
tracks the goal and context package; the underlying tmux pane lives in the
``sessions`` table.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from atc.session.ace import (
    ATC_TMUX_SESSION,
    _ensure_tmux_session,
    _kill_pane,
    _send_keys,
    _spawn_pane,
)
from atc.session.state_machine import SessionStatus, transition
from atc.state import db as db_ops

if TYPE_CHECKING:
    import aiosqlite

    from atc.core.events import EventBus

logger = logging.getLogger(__name__)


async def start_leader(
    conn: aiosqlite.Connection,
    project_id: str,
    *,
    goal: str | None = None,
    event_bus: EventBus | None = None,
) -> str:
    """Start the Leader session for a project.

    Creates a session of type ``manager``, spawns its tmux pane, and links
    it to the leader row.  Returns the session id.
    """
    leader = await db_ops.get_leader_by_project(conn, project_id)
    if leader is None:
        leader = await db_ops.create_leader(conn, project_id, goal=goal)

    # If leader already has an active session, just return it
    if leader.session_id:
        existing = await db_ops.get_session(conn, leader.session_id)
        if existing and existing.status not in (
            SessionStatus.ERROR.value,
            SessionStatus.DISCONNECTED.value,
        ):
            return leader.session_id

    # Create manager session (DB-first)
    project = await db_ops.get_project(conn, project_id)
    name = f"leader-{project.name}" if project else f"leader-{project_id[:8]}"

    session = await db_ops.create_session(
        conn,
        project_id=project_id,
        session_type="manager",
        name=name,
        status=SessionStatus.CONNECTING.value,
    )

    if event_bus:
        await event_bus.publish(
            "session_created",
            {"session_id": session.id, "session_type": "manager", "project_id": project_id},
        )

    try:
        await _ensure_tmux_session(ATC_TMUX_SESSION)
        pane_id = await _spawn_pane(ATC_TMUX_SESSION)
        await db_ops.update_session_tmux(conn, session.id, ATC_TMUX_SESSION, pane_id)

        await transition(session.id, SessionStatus.CONNECTING, SessionStatus.IDLE, event_bus)
        await db_ops.update_session_status(conn, session.id, SessionStatus.IDLE.value)
    except Exception:
        logger.exception("Failed to spawn leader pane for project %s", project_id)
        await db_ops.update_session_status(conn, session.id, SessionStatus.ERROR.value)
        if event_bus:
            await event_bus.publish(
                "session_status_changed",
                {
                    "session_id": session.id,
                    "previous_status": SessionStatus.CONNECTING.value,
                    "new_status": SessionStatus.ERROR.value,
                },
            )
        return session.id

    # Link session to leader row
    await conn.execute(
        "UPDATE leaders SET session_id = ?, status = 'managing',"
        " goal = ?, updated_at = datetime('now') WHERE id = ?",
        (session.id, goal or leader.goal, leader.id),
    )
    await conn.commit()

    return session.id


async def stop_leader(
    conn: aiosqlite.Connection,
    project_id: str,
    *,
    event_bus: EventBus | None = None,
) -> None:
    """Stop the Leader session for a project."""
    leader = await db_ops.get_leader_by_project(conn, project_id)
    if leader is None or leader.session_id is None:
        return

    session = await db_ops.get_session(conn, leader.session_id)
    if session is None:
        return

    # Kill tmux pane
    if session.tmux_pane:
        await _kill_pane(session.tmux_pane)

    # Transition session to idle (or just set directly since we killed the pane)
    await db_ops.update_session_status(conn, session.id, SessionStatus.IDLE.value)

    if event_bus:
        await event_bus.publish(
            "session_status_changed",
            {
                "session_id": session.id,
                "previous_status": session.status,
                "new_status": SessionStatus.IDLE.value,
            },
        )

    # Unlink from leader
    await conn.execute(
        "UPDATE leaders SET session_id = NULL, status = 'idle',"
        " updated_at = datetime('now') WHERE id = ?",
        (leader.id,),
    )
    await conn.commit()


async def send_leader_message(
    conn: aiosqlite.Connection,
    project_id: str,
    message: str,
    *,
    event_bus: EventBus | None = None,
) -> None:
    """Send a message to the Leader's tmux pane."""
    leader = await db_ops.get_leader_by_project(conn, project_id)
    if leader is None or leader.session_id is None:
        raise ValueError(f"No active leader for project {project_id}")

    session = await db_ops.get_session(conn, leader.session_id)
    if session is None or session.tmux_pane is None:
        raise ValueError("Leader session has no tmux pane")

    current = SessionStatus(session.status)
    if current in (SessionStatus.IDLE, SessionStatus.WAITING):
        await transition(session.id, current, SessionStatus.WORKING, event_bus)
        await db_ops.update_session_status(conn, session.id, SessionStatus.WORKING.value)

    await _send_keys(session.tmux_pane, message)

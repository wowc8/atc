"""Ace session lifecycle — create / start / stop / destroy.

Follows the DB-first pattern:
  1. Write session row to DB (status: connecting)
  2. Publish ``session_created`` event
  3. Spawn tmux pane
  4a. Success → update row (status: idle, tmux_pane: <id>)
  4b. Failure → update row (status: error)
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from atc.session.state_machine import (
    SessionStatus,
    transition,
)
from atc.state import db as db_ops

if TYPE_CHECKING:
    import aiosqlite

    from atc.core.events import EventBus

logger = logging.getLogger(__name__)

# Default tmux session name used for ATC panes
ATC_TMUX_SESSION = "atc"


# ---------------------------------------------------------------------------
# tmux helpers (thin wrappers around subprocess)
# ---------------------------------------------------------------------------


async def _tmux_run(*args: str) -> str:
    """Run a tmux command and return stdout."""
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"tmux {' '.join(args)} failed: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def _ensure_tmux_session(session_name: str) -> None:
    """Create the tmux session if it doesn't already exist."""
    try:
        await _tmux_run("has-session", "-t", session_name)
    except RuntimeError:
        await _tmux_run("new-session", "-d", "-s", session_name)


async def _spawn_pane(session_name: str, command: str | None = None) -> str:
    """Split a new pane in *session_name* and return its pane id (e.g. ``%5``)."""
    args = ["split-window", "-t", session_name, "-d", "-P", "-F", "#{pane_id}"]
    if command:
        args.extend([command])
    pane_id = await _tmux_run(*args)
    return pane_id


async def _kill_pane(pane_id: str) -> None:
    """Kill a tmux pane by id."""
    try:
        await _tmux_run("kill-pane", "-t", pane_id)
    except RuntimeError:
        logger.warning("Failed to kill pane %s (may already be dead)", pane_id)


async def _pane_is_alive(pane_id: str) -> bool:
    """Check if a tmux pane still exists."""
    try:
        await _tmux_run("has-session", "-t", pane_id)
        return True
    except RuntimeError:
        return False


async def _send_keys(pane_id: str, keys: str) -> None:
    """Send keystrokes to a tmux pane."""
    await _tmux_run("send-keys", "-t", pane_id, keys, "Enter")


# ---------------------------------------------------------------------------
# Ace lifecycle
# ---------------------------------------------------------------------------


async def create_ace(
    conn: aiosqlite.Connection,
    project_id: str,
    name: str,
    *,
    task_id: str | None = None,
    host: str | None = None,
    event_bus: EventBus | None = None,
) -> str:
    """Create an ace session (DB-first). Returns the session id.

    The session is created with status ``connecting`` and a tmux pane is
    spawned.  On success the status moves to ``idle``; on failure to ``error``.
    """
    # Step 1: DB row first
    session = await db_ops.create_session(
        conn,
        project_id=project_id,
        session_type="ace",
        name=name,
        task_id=task_id,
        host=host,
        status=SessionStatus.CONNECTING.value,
    )

    # Step 2: publish creation event
    if event_bus:
        await event_bus.publish(
            "session_created",
            {"session_id": session.id, "session_type": "ace", "project_id": project_id},
        )

    # Step 3: spawn tmux pane
    try:
        await _ensure_tmux_session(ATC_TMUX_SESSION)
        pane_id = await _spawn_pane(ATC_TMUX_SESSION)
        await db_ops.update_session_tmux(conn, session.id, ATC_TMUX_SESSION, pane_id)

        # Step 4a: success → idle
        await transition(
            session.id, SessionStatus.CONNECTING, SessionStatus.IDLE, event_bus
        )
        await db_ops.update_session_status(conn, session.id, SessionStatus.IDLE.value)
    except Exception:
        # Step 4b: failure → error
        logger.exception("Failed to spawn tmux pane for session %s", session.id)
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


async def start_ace(
    conn: aiosqlite.Connection,
    session_id: str,
    *,
    instruction: str | None = None,
    event_bus: EventBus | None = None,
) -> None:
    """Start an ace session — send an instruction to its tmux pane.

    Transitions: idle|waiting → working.
    """
    session = await db_ops.get_session(conn, session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    current = SessionStatus(session.status)
    await transition(session_id, current, SessionStatus.WORKING, event_bus)
    await db_ops.update_session_status(conn, session_id, SessionStatus.WORKING.value)

    if instruction and session.tmux_pane:
        await _send_keys(session.tmux_pane, instruction)


async def stop_ace(
    conn: aiosqlite.Connection,
    session_id: str,
    *,
    event_bus: EventBus | None = None,
) -> None:
    """Stop an ace session — transition to paused.

    Transitions: working|waiting → paused.
    """
    session = await db_ops.get_session(conn, session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    current = SessionStatus(session.status)
    await transition(session_id, current, SessionStatus.PAUSED, event_bus)
    await db_ops.update_session_status(conn, session_id, SessionStatus.PAUSED.value)


async def destroy_ace(
    conn: aiosqlite.Connection,
    session_id: str,
    *,
    event_bus: EventBus | None = None,
) -> None:
    """Destroy an ace session — kill tmux pane and delete DB row."""
    session = await db_ops.get_session(conn, session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    # Kill tmux pane if present
    if session.tmux_pane:
        await _kill_pane(session.tmux_pane)

    # Delete from DB
    await db_ops.delete_session(conn, session_id)

    if event_bus:
        await event_bus.publish(
            "session_destroyed",
            {"session_id": session_id, "session_type": "ace", "project_id": session.project_id},
        )


# ---------------------------------------------------------------------------
# Verification loop (creation reliability)
# ---------------------------------------------------------------------------


async def verify_session(
    conn: aiosqlite.Connection,
    session_id: str,
    *,
    event_bus: EventBus | None = None,
) -> bool:
    """Run the three-phase verification check.

    Returns ``True`` if the session appears healthy, ``False`` otherwise.
    Called by the orchestrator after spawning.
    """
    session = await db_ops.get_session(conn, session_id)
    if session is None:
        return False

    # Phase 1 (t+10s equivalent): Is it alive?
    if session.status == SessionStatus.ERROR.value:
        return False

    if session.tmux_pane and not await _pane_is_alive(session.tmux_pane):
        logger.warning("Session %s: tmux pane %s is dead", session_id, session.tmux_pane)
        await transition(
            session_id,
            SessionStatus(session.status),
            SessionStatus.DISCONNECTED,
            event_bus,
        )
        await db_ops.update_session_status(conn, session_id, SessionStatus.DISCONNECTED.value)
        return False

    return True

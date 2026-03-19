"""Session reconnection logic.

On startup, ATC finds sessions that were active at last shutdown and
attempts to reconnect them by verifying their tmux panes are alive and
the TUI is ready (alternate_on == False).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from atc.agents.deploy import TowerDeploySpec, deploy_tower_files
from atc.agents.factory import get_launch_command
from atc.session.ace import (
    ATC_TMUX_SESSION,
    _ensure_tmux_session,
    _pane_is_alive,
    _spawn_pane,
)
from atc.session.state_machine import SessionStatus, transition
from atc.state import db as db_ops

if TYPE_CHECKING:
    import aiosqlite

    from atc.core.events import EventBus

logger = logging.getLogger(__name__)


async def reconnect_session(
    conn: aiosqlite.Connection,
    session_id: str,
    *,
    event_bus: EventBus | None = None,
) -> bool:
    """Attempt to reconnect a single session.

    Returns ``True`` if the session was successfully reconnected.

    Steps:
      1. Check if the tmux pane is still alive
      2. If alive, verify TUI readiness and transition to idle
      3. If dead, attempt to respawn the pane
    """
    session = await db_ops.get_session(conn, session_id)
    if session is None:
        logger.warning("Cannot reconnect session %s: not found in DB", session_id)
        return False

    current = SessionStatus(session.status)

    # Already in a terminal state — skip
    if current == SessionStatus.ERROR:
        logger.info("Skipping reconnect for session %s: status is error", session_id)
        return False

    # Check if pane is still alive
    if session.tmux_pane and await _pane_is_alive(session.tmux_pane):
        logger.info("Session %s: tmux pane %s still alive", session_id, session.tmux_pane)
        # Transition back to idle if it was working/waiting/disconnected
        if current in (
            SessionStatus.WORKING,
            SessionStatus.WAITING,
            SessionStatus.DISCONNECTED,
            SessionStatus.CONNECTING,
        ):
            target = SessionStatus.IDLE
            # disconnected → idle not directly valid; go through connecting
            if current == SessionStatus.DISCONNECTED:
                await transition(session_id, current, SessionStatus.CONNECTING, event_bus)
                await db_ops.update_session_status(conn, session_id, SessionStatus.CONNECTING.value)
                current = SessionStatus.CONNECTING
            await transition(session_id, current, target, event_bus)
            await db_ops.update_session_status(conn, session_id, target.value)
        return True

    # Pane is dead — try to respawn
    logger.info("Session %s: pane dead, attempting respawn", session_id)

    # Mark as connecting
    if current != SessionStatus.CONNECTING:
        if current in (
            SessionStatus.DISCONNECTED,
            SessionStatus.IDLE,
            SessionStatus.ERROR,
        ):
            await transition(session_id, current, SessionStatus.CONNECTING, event_bus)
        else:
            # For working/waiting/paused, go to disconnected first
            try:
                await transition(session_id, current, SessionStatus.DISCONNECTED, event_bus)
                await db_ops.update_session_status(
                    conn, session_id, SessionStatus.DISCONNECTED.value
                )
            except Exception:
                pass
            await transition(
                session_id, SessionStatus.DISCONNECTED, SessionStatus.CONNECTING, event_bus
            )
        await db_ops.update_session_status(conn, session_id, SessionStatus.CONNECTING.value)

    try:
        # Look up the project's agent provider to get the correct launch command
        launch_cmd: str | None = None
        working_dir: str | None = None
        project = None
        if session.project_id:
            project = await db_ops.get_project(conn, session.project_id)
            if project:
                provider = project.agent_provider or "claude_code"
                launch_cmd = get_launch_command(provider)
                working_dir = project.repo_path

        # Tower sessions need their identity files (CLAUDE.md, settings)
        # re-deployed so Claude Code picks up the Tower role on respawn.
        if getattr(session, "session_type", None) == "tower" and project:
            spec = TowerDeploySpec(
                session_id=session_id,
                project_name=project.name if project else "",
                project_id=session.project_id,
                repo_path=working_dir,
                github_repo=project.github_repo if project else None,
            )
            deployed = deploy_tower_files(spec)
            working_dir = str(deployed.root)
            logger.info("Re-deployed tower config for %s → %s", session_id, deployed.root)

        await _ensure_tmux_session(ATC_TMUX_SESSION)
        pane_id = await _spawn_pane(
            ATC_TMUX_SESSION,
            launch_cmd,
            working_dir=working_dir,
        )
        await db_ops.update_session_tmux(conn, session_id, ATC_TMUX_SESSION, pane_id)

        await transition(session_id, SessionStatus.CONNECTING, SessionStatus.IDLE, event_bus)
        await db_ops.update_session_status(conn, session_id, SessionStatus.IDLE.value)
        logger.info("Session %s: respawned as pane %s", session_id, pane_id)
        return True
    except Exception:
        logger.exception("Failed to respawn pane for session %s", session_id)
        await db_ops.update_session_status(conn, session_id, SessionStatus.ERROR.value)
        if event_bus:
            await event_bus.publish(
                "session_status_changed",
                {
                    "session_id": session_id,
                    "previous_status": SessionStatus.CONNECTING.value,
                    "new_status": SessionStatus.ERROR.value,
                },
            )
        return False


async def reconnect_all(
    conn: aiosqlite.Connection,
    *,
    event_bus: EventBus | None = None,
) -> dict[str, bool]:
    """Reconnect all sessions that were active at last shutdown.

    Returns a mapping of session_id → success boolean.
    """
    sessions = await db_ops.list_active_sessions(conn)
    logger.info("Found %d sessions to reconnect", len(sessions))

    results: dict[str, bool] = {}
    for session in sessions:
        ok = await reconnect_session(conn, session.id, event_bus=event_bus)
        results[session.id] = ok

    succeeded = sum(1 for v in results.values() if v)
    logger.info("Reconnected %d/%d sessions", succeeded, len(results))
    return results

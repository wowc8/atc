"""Tower session lifecycle — start / stop / message.

Tower gets its own independent Claude Code session (separate from Leader).
Tower is the top-level controller that talks directly to the user through
its terminal. Leader is a project-scoped agent that Tower delegates to.

Uses the same tmux infrastructure as aces and leaders.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from atc.agents.deploy import TowerDeploySpec, deploy_tower_files
from atc.agents.factory import get_launch_command
from atc.session.ace import (
    ATC_TMUX_SESSION,
    _ensure_tmux_session,
    _kill_pane,
    _pane_is_alive,
    _send_keys,
    _spawn_pane,
)
from atc.session.state_machine import SessionStatus, transition
from atc.state import db as db_ops

if TYPE_CHECKING:
    import aiosqlite

    from atc.core.events import EventBus

logger = logging.getLogger(__name__)


async def start_tower_session(
    conn: aiosqlite.Connection,
    project_id: str,
    *,
    event_bus: EventBus | None = None,
) -> str:
    """Start Tower's own Claude Code session for a project.

    Creates a session of type ``tower``, spawns a tmux pane running
    ``claude``, and returns the session id.  This session is independent
    of the Leader session.
    """
    project = await db_ops.get_project(conn, project_id)
    name = f"tower-{project.name}" if project else f"tower-{project_id[:8]}"

    # Check for existing tower session — validate tmux pane is actually alive
    existing = await db_ops.list_sessions(conn, project_id=project_id, session_type="tower")
    for sess in existing:
        if sess.status in (SessionStatus.ERROR.value, SessionStatus.DISCONNECTED.value):
            continue
        # Verify the tmux pane is still alive; stale sessions from a previous
        # app run may have a non-terminal DB status but a dead pane.
        if sess.tmux_pane and await _pane_is_alive(sess.tmux_pane):
            return sess.id
        # Pane is dead or missing — mark session as disconnected so we create fresh
        logger.warning(
            "Tower session %s has dead/missing tmux pane %s — discarding",
            sess.id,
            sess.tmux_pane,
        )
        await db_ops.update_session_status(conn, sess.id, SessionStatus.DISCONNECTED.value)
        if event_bus:
            await event_bus.publish(
                "session_status_changed",
                {
                    "session_id": sess.id,
                    "previous_status": sess.status,
                    "new_status": SessionStatus.DISCONNECTED.value,
                },
            )

    session = await db_ops.create_session(
        conn,
        project_id=project_id,
        session_type="tower",
        name=name,
        status=SessionStatus.CONNECTING.value,
    )

    if event_bus:
        await event_bus.publish(
            "session_created",
            {"session_id": session.id, "session_type": "tower", "project_id": project_id},
        )

    try:
        provider = project.agent_provider if project else "claude_code"
        launch_cmd = get_launch_command(provider)
        working_dir = project.repo_path if project and project.repo_path else None

        # Deploy config files (CLAUDE.md, hooks, settings.json) before launch
        spec = TowerDeploySpec(
            session_id=session.id,
            project_name=project.name if project else "",
            project_id=project_id,
            repo_path=working_dir,
            github_repo=project.github_repo if project else None,
        )
        deployed = deploy_tower_files(spec)
        logger.info("Deployed tower config for %s → %s", session.id, deployed.root)

        # Use repo path if available, otherwise the deployed config directory
        if not working_dir:
            working_dir = str(deployed.root)

        await _ensure_tmux_session(ATC_TMUX_SESSION)
        pane_id = await _spawn_pane(
            ATC_TMUX_SESSION,
            launch_cmd,
            working_dir=working_dir,
        )
        await db_ops.update_session_tmux(conn, session.id, ATC_TMUX_SESSION, pane_id)

        await transition(session.id, SessionStatus.CONNECTING, SessionStatus.IDLE, event_bus)
        await db_ops.update_session_status(conn, session.id, SessionStatus.IDLE.value)
    except Exception as exc:
        logger.exception("Failed to spawn tower pane for project %s", project_id)
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
        raise RuntimeError(str(exc)) from exc

    return session.id


async def stop_tower_session(
    conn: aiosqlite.Connection,
    session_id: str,
    *,
    event_bus: EventBus | None = None,
) -> None:
    """Stop Tower's Claude Code session."""
    session = await db_ops.get_session(conn, session_id)
    if session is None:
        return

    if session.tmux_pane:
        await _kill_pane(session.tmux_pane)

    await db_ops.update_session_status(conn, session.id, SessionStatus.DISCONNECTED.value)

    if event_bus:
        await event_bus.publish(
            "session_status_changed",
            {
                "session_id": session.id,
                "previous_status": session.status,
                "new_status": SessionStatus.DISCONNECTED.value,
            },
        )


async def send_tower_message(
    conn: aiosqlite.Connection,
    session_id: str,
    message: str,
    *,
    event_bus: EventBus | None = None,
) -> None:
    """Send a message to Tower's tmux pane."""
    session = await db_ops.get_session(conn, session_id)
    if session is None or session.tmux_pane is None:
        raise ValueError("Tower session has no tmux pane")

    current = SessionStatus(session.status)
    if current in (SessionStatus.IDLE, SessionStatus.WAITING):
        await transition(session.id, current, SessionStatus.WORKING, event_bus)
        await db_ops.update_session_status(conn, session.id, SessionStatus.WORKING.value)

    await _send_keys(session.tmux_pane, message)

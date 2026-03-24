"""Tower session lifecycle — start / stop / message.

Tower gets its own independent Claude Code session (separate from Leader).
Tower is the top-level controller that talks directly to the user through
its terminal. Leader is a project-scoped agent that Tower delegates to.

Uses the same tmux infrastructure as aces and leaders.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from atc.agents.deploy import _DEFAULT_STAGING_ROOT, TowerDeploySpec, deploy_tower_files
from atc.agents.factory import get_launch_command
from atc.session.ace import (
    ATC_TMUX_SESSION,
    _accept_trust_dialog,
    _ensure_tmux_session,
    _kill_pane,
    _pane_is_alive,
    _send_keys,
    _spawn_pane,
)
from atc.session.state_machine import SessionStatus, transition
from atc.state import db as db_ops

if TYPE_CHECKING:
    import aiosqlite  # type: ignore[import-not-found]

    from atc.core.events import EventBus

logger = logging.getLogger(__name__)


async def _resolve_tower_project_id(conn: "aiosqlite.Connection") -> str:
    """Return a project_id suitable for anchoring a Tower session.

    Tower sessions require a project_id due to the DB FK constraint, but
    Tower itself is global (not project-scoped).  When no explicit project_id
    is supplied we use the first active project, or create a sentinel
    'Tower Workspace' project so Tower can start on a clean DB.
    """
    cursor = await conn.execute(
        "SELECT id FROM projects WHERE status = 'active' ORDER BY position ASC, created_at ASC LIMIT 1"
    )
    row = await cursor.fetchone()
    if row:
        return row[0]

    # No projects yet — create a sentinel project so Tower can boot
    project = await db_ops.create_project(
        conn,
        "Tower Workspace",
        description="Auto-created by Tower on first start. Safe to delete once you add real projects.",
    )
    await conn.commit()
    logger.info("Created sentinel Tower Workspace project %s", project.id)
    return project.id


async def start_tower_session(
    conn: "aiosqlite.Connection",
    project_id: str | None = None,
    *,
    event_bus: "EventBus | None" = None,
) -> str:
    """Start Tower's own Claude Code session.

    Tower is global — not project-scoped — but the sessions table requires a
    project_id FK.  When project_id is omitted we resolve one automatically
    (first active project, or a sentinel 'Tower Workspace' project).

    Creates a session of type ``tower``, spawns a tmux pane running
    ``claude``, and returns the session id.  This session is independent
    of the Leader session.
    """
    if project_id is None:
        project_id = await _resolve_tower_project_id(conn)

    project = await db_ops.get_project(conn, project_id)
    name = f"tower-{project.name}" if project else f"tower-{project_id[:8]}"

    # Check for existing tower session — validate tmux pane is actually alive
    # AND that the Tower identity files are deployed at the working directory.
    # Search ALL tower sessions (not just the anchor project) so we don't
    # create a new Tower session every restart when the anchor project changes.
    cursor = await conn.execute(
        "SELECT * FROM sessions WHERE session_type = 'tower' ORDER BY created_at DESC LIMIT 20"
    )
    rows = await cursor.fetchall()
    from atc.state.db import _row_to_session
    existing = [_row_to_session(r) for r in rows]
    for sess in existing:
        if sess.status in (SessionStatus.ERROR.value, SessionStatus.DISCONNECTED.value):
            continue
        # Verify the tmux pane is still alive; stale sessions from a previous
        # app run may have a non-terminal DB status but a dead pane.
        if sess.tmux_pane and await _pane_is_alive(sess.tmux_pane):
            # Verify CLAUDE.md is deployed — a stale pane from a prior app run
            # may have been launched without Tower identity files.
            staging_dir = Path(_DEFAULT_STAGING_ROOT) / sess.id
            if (staging_dir / "CLAUDE.md").is_file():
                logger.info(
                    "Reusing existing tower session %s (CLAUDE.md present at %s)",
                    sess.id,
                    staging_dir,
                )
                return sess.id
            # CLAUDE.md missing — kill stale pane and create fresh session
            logger.warning(
                "Tower session %s has live pane but CLAUDE.md missing at %s — killing stale pane",
                sess.id,
                staging_dir,
            )
            await _kill_pane(sess.tmux_pane)
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

        # Always use the staging directory so Claude Code finds the deployed
        # CLAUDE.md (Tower identity) and .claude/settings.json (hooks, model).
        # Tower never writes code directly — it delegates through Leaders —
        # so it doesn't need to start in the repo directory.
        working_dir = str(deployed.root)

        # --- Runtime debug: verify CLAUDE.md is present at working_dir ---
        _log_working_dir_contents(working_dir, session.id, "start_tower_session")

        await _ensure_tmux_session(ATC_TMUX_SESSION)
        pane_id = await _spawn_pane(
            ATC_TMUX_SESSION,
            launch_cmd,
            working_dir=working_dir,
        )
        await _accept_trust_dialog(pane_id)
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

    # Reject sends to sessions that are clearly not running
    if current in (SessionStatus.ERROR, SessionStatus.DISCONNECTED):
        raise ValueError(f"Tower session is {current.value} — stop and restart")

    if current in (SessionStatus.IDLE, SessionStatus.WAITING):
        await transition(session.id, current, SessionStatus.WORKING, event_bus)
        await db_ops.update_session_status(conn, session.id, SessionStatus.WORKING.value)

    # Verify the pane is alive before sending keys
    if not await _pane_is_alive(session.tmux_pane):
        raise ValueError("Tower tmux pane is dead — stop and restart")

    await _send_keys(session.tmux_pane, message)


def _log_working_dir_contents(working_dir: str, session_id: str, caller: str) -> None:
    """Log the contents of the working directory for runtime debugging.

    Verifies that CLAUDE.md and .claude/settings.json are present at the
    path that will be passed as -c to tmux (i.e. where Claude Code starts).
    """
    logger.warning(
        "=== TOWER DEBUG [%s] session=%s ===\n" "  working_dir: %s\n" "  working_dir exists: %s",
        caller,
        session_id,
        working_dir,
        os.path.isdir(working_dir),
    )

    if not os.path.isdir(working_dir):
        logger.warning("  working_dir DOES NOT EXIST — Claude Code will NOT find CLAUDE.md")
        return

    # List all files at the root of working_dir
    try:
        entries = os.listdir(working_dir)
        logger.warning("  Files at working_dir root: %s", entries)
    except OSError as exc:
        logger.warning("  Failed to list working_dir: %s", exc)
        return

    # Check CLAUDE.md specifically
    claude_md_path = os.path.join(working_dir, "CLAUDE.md")
    if os.path.isfile(claude_md_path):
        try:
            with open(claude_md_path) as f:
                first_lines = "".join(f.readlines()[:5])
            logger.warning(
                "  CLAUDE.md FOUND (%d bytes), first lines:\n%s",
                os.path.getsize(claude_md_path),
                first_lines,
            )
        except OSError as exc:
            logger.warning("  CLAUDE.md exists but could not read: %s", exc)
    else:
        logger.warning("  CLAUDE.md NOT FOUND at %s", claude_md_path)

    # Check .claude/settings.json
    settings_path = os.path.join(working_dir, ".claude", "settings.json")
    if os.path.isfile(settings_path):
        logger.warning("  .claude/settings.json FOUND (%d bytes)", os.path.getsize(settings_path))
    else:
        logger.warning("  .claude/settings.json NOT FOUND at %s", settings_path)

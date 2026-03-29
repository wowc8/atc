"""Session reconnection logic.

On startup, ATC finds sessions that were active at last shutdown and
attempts to reconnect them by verifying their tmux panes are alive and
the TUI is ready (alternate_on == False).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from atc.agents.deploy import TowerDeploySpec, deploy_tower_files
from atc.agents.factory import get_launch_command
from atc.leader.leader import _build_manager_deploy_spec
from atc.session.ace import (
    ATC_TMUX_SESSION,
    _accept_trust_dialog,
    _ensure_tmux_session,
    _kill_pane,
    _pane_is_alive,
    _spawn_pane,
)
from atc.session.state_machine import SessionStatus, transition
from atc.state import db as db_ops

if TYPE_CHECKING:
    import aiosqlite  # type: ignore[import-not-found]

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
        # Tower sessions must always be respawned with fresh config so Claude
        # Code picks up the latest CLAUDE.md identity and settings.  A stale
        # pane from a prior app run may have been launched with outdated (or
        # missing) Tower identity files, causing "no specific ATC role".
        session_type = getattr(session, "session_type", None)
        if session_type in ("tower", "manager"):
            logger.info(
                "Session %s: %s pane %s alive but killing to respawn with fresh config",
                session_id,
                session_type,
                session.tmux_pane,
            )
            await _kill_pane(session.tmux_pane)
            # Fall through to the respawn logic below
        else:
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
                    await db_ops.update_session_status(
                        conn, session_id, SessionStatus.CONNECTING.value
                    )
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

        # Manager (Leader) sessions also need config re-deployed so Claude
        # Code picks up the Leader identity, hooks, and settings on respawn.
        elif getattr(session, "session_type", None) == "manager" and project:
            from atc.agents.deploy import deploy_manager_files

            leader = await db_ops.get_leader_by_project(conn, session.project_id)
            import os as _os
            _api_url = _os.environ.get("ATC_API_URL", "")
            mgr_spec = _build_manager_deploy_spec(
                leader_id=leader.id if leader else session_id,
                project_name=project.name if project else "",
                goal=(leader.goal or "") if leader else "",
                repo_path=working_dir,
                github_repo=project.github_repo if project else None,
                api_base_url=_api_url,
            )
            mgr_spec.session_id = session_id
            deployed = deploy_manager_files(mgr_spec)
            working_dir = str(deployed.root)
            logger.info("Re-deployed manager config for %s → %s", session_id, deployed.root)

        # --- Runtime debug: verify working_dir contents before spawn ---
        if working_dir:
            _log_reconnect_working_dir(working_dir, session_id)

        await _ensure_tmux_session(ATC_TMUX_SESSION)
        pane_id = await _spawn_pane(
            ATC_TMUX_SESSION,
            launch_cmd,
            working_dir=working_dir,
        )
        await _accept_trust_dialog(pane_id)
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
        # Tower sessions are managed exclusively by the TowerController restore
        # logic in the lifespan (step 8). Reconnecting them here would respawn
        # the pane and transition state to MANAGING before the restore check
        # can set it to IDLE (when there is no active goal). Skip them.
        session_type = getattr(session, "session_type", None)
        if session_type in ("tower", "manager"):
            logger.info(
                "Skipping reconnect for tower session %s — handled by TowerController restore",
                session.id,
            )
            results[session.id] = True
            continue
        ok = await reconnect_session(conn, session.id, event_bus=event_bus)
        results[session.id] = ok

    succeeded = sum(1 for v in results.values() if v)
    logger.info("Reconnected %d/%d sessions", succeeded, len(results))
    return results


def _log_reconnect_working_dir(working_dir: str, session_id: str) -> None:
    """Log working_dir contents during reconnection for runtime debugging."""
    logger.warning(
        "=== RECONNECT DEBUG session=%s ===\n" "  working_dir: %s\n" "  exists: %s",
        session_id,
        working_dir,
        os.path.isdir(working_dir),
    )
    if os.path.isdir(working_dir):
        try:
            entries = os.listdir(working_dir)
            logger.warning("  Files at working_dir root: %s", entries)
        except OSError as exc:
            logger.warning("  Failed to list working_dir: %s", exc)

        claude_md = os.path.join(working_dir, "CLAUDE.md")
        if os.path.isfile(claude_md):
            logger.warning("  CLAUDE.md FOUND (%d bytes)", os.path.getsize(claude_md))
        else:
            logger.warning("  CLAUDE.md NOT FOUND at %s", claude_md)
    else:
        logger.warning("  working_dir DOES NOT EXIST")

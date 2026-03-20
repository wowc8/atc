"""Leader session lifecycle — start / stop / message.

A Leader is one per project.  It uses the same session infrastructure as aces
but has session_type ``manager``.  The Leader row in the ``leaders`` table
tracks the goal and context package; the underlying tmux pane lives in the
``sessions`` table.

The start flow deploys config files (CLAUDE.md, hooks) via ``deploy.py``
before spawning the tmux pane so that Claude Code picks up the Leader's
instructions automatically.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from atc.agents.deploy import ManagerDeploySpec, deploy_manager_files
from atc.agents.factory import get_launch_command
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



def _build_manager_deploy_spec(
    leader_id: str,
    project_name: str,
    goal: str,
    *,
    project_id: str | None = None,
    repo_path: str | None = None,
    github_repo: str | None = None,
    context_entries: list[dict[str, Any]] | None = None,
) -> ManagerDeploySpec:
    """Build a ManagerDeploySpec from project metadata."""
    return ManagerDeploySpec(
        leader_id=leader_id,
        project_name=project_name,
        goal=goal,
        project_id=project_id,
        repo_path=repo_path,
        github_repo=github_repo,
        context_entries=context_entries or [],
    )


async def start_leader(
    conn: aiosqlite.Connection,
    project_id: str,
    *,
    goal: str | None = None,
    event_bus: EventBus | None = None,
    context_package: dict[str, Any] | None = None,
) -> str:
    """Start the Leader session for a project.

    Creates a session of type ``manager``, deploys config files via
    ``deploy.py``, spawns a tmux pane running ``claude``, and links
    it to the leader row.  Returns the session id.

    Args:
        context_package: The assembled context package from Tower.
            Used to populate the ManagerDeploySpec with project metadata
            and context entries.
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
        # Deploy config files (CLAUDE.md, hooks, settings.json) before launch
        ctx = context_package or {}
        spec = _build_manager_deploy_spec(
            leader_id=leader.id,
            project_name=ctx.get("project_name") or (project.name if project else ""),
            goal=goal or leader.goal or "",
            project_id=project_id,
            repo_path=ctx.get("repo_path") or (project.repo_path if project else None),
            github_repo=ctx.get("github_repo") or (project.github_repo if project else None),
            context_entries=ctx.get("context_entries"),
        )
        # Pass the real session_id so hooks reference the correct ID
        spec.session_id = session.id
        deployed = deploy_manager_files(spec)
        logger.info(
            "Deployed manager config for leader %s (session %s) → %s",
            leader.id,
            session.id,
            deployed.root,
        )

        # Use the staging directory so Claude Code finds the deployed
        # CLAUDE.md (Leader role/goal) and .claude/settings.json (hooks, model).
        working_dir = str(deployed.root)

        launch_cmd = get_launch_command(
            project.agent_provider if project else "claude_code",
        )

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
        raise RuntimeError(str(exc)) from exc

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

    # Reject sends to sessions that are clearly not running
    if current in (SessionStatus.ERROR, SessionStatus.DISCONNECTED):
        raise ValueError(
            f"Leader session is {current.value} — stop and restart the leader"
        )

    if current in (SessionStatus.IDLE, SessionStatus.WAITING):
        await transition(session.id, current, SessionStatus.WORKING, event_bus)
        await db_ops.update_session_status(conn, session.id, SessionStatus.WORKING.value)

    # Verify the pane is alive before sending keys
    from atc.session.ace import _pane_is_alive

    if not await _pane_is_alive(session.tmux_pane):
        raise ValueError(
            "Leader tmux pane is dead — stop and restart the leader"
        )

    await _send_keys(session.tmux_pane, message)

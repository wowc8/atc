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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from atc.agents.deploy import ManagerDeploySpec, deploy_manager_files
from atc.runtime.models import InstructionRequest, RuntimeDeliveryResult, StopRoleRequest
from atc.runtime.service import RuntimeService
from atc.session.ace import _accept_trust_dialog as _accept_trust_dialog  # noqa: F401
from atc.session.ace import _persist_delivery_trace_events, _spawn_provider_session
from atc.session.state_machine import SessionStatus, transition
from atc.state import db as db_ops
from atc.state.db import get_connection_app_state

if TYPE_CHECKING:
    import aiosqlite  # type: ignore[import-not-found]

    from atc.config import AgentProviderConfig
    from atc.core.events import EventBus

logger = logging.getLogger(__name__)


def _current_provider_config(conn: aiosqlite.Connection) -> AgentProviderConfig:
    settings = get_connection_app_state(conn)
    if settings is not None and getattr(settings, "settings", None) is not None:
        return settings.settings.agent_provider
    from atc.config import load_settings

    return load_settings().agent_provider


def _build_manager_deploy_spec(
    leader_id: str,
    project_name: str,
    goal: str,
    *,
    project_id: str | None = None,
    session_id: str | None = None,
    repo_path: str | None = None,
    github_repo: str | None = None,
    context_entries: list[dict[str, Any]] | None = None,
    api_base_url: str = "",
) -> ManagerDeploySpec:
    """Build a ManagerDeploySpec from project metadata."""
    return ManagerDeploySpec(
        leader_id=leader_id,
        project_name=project_name,
        goal=goal,
        project_id=project_id,
        session_id=session_id,
        repo_path=repo_path,
        github_repo=github_repo,
        context_entries=context_entries or [],
        api_base_url=api_base_url,
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

    project = await db_ops.get_project(conn, project_id)
    name = f"leader-{project.name}" if project else f"leader-{project_id[:8]}"

    provider_cfg = _current_provider_config(conn)
    provider = provider_cfg.default

    # If leader already has an active session, only reuse it when the stamped
    # provider still matches the current desired provider.
    if leader.session_id:
        existing = await db_ops.get_session(conn, leader.session_id)
        if existing and existing.status not in (
            SessionStatus.ERROR.value,
            SessionStatus.DISCONNECTED.value,
        ):
            if existing.provider == provider:
                return leader.session_id
            logger.warning(
                "Leader session %s provider mismatch on reuse "
                "(session=%s current=%s) — refusing reuse",
                leader.session_id,
                existing.provider,
                provider,
            )
            await db_ops.update_session_status(
                conn, leader.session_id, SessionStatus.DISCONNECTED.value
            )
            await conn.execute(
                "UPDATE leaders SET session_id = NULL, status = 'idle',"
                " updated_at = datetime('now') WHERE id = ?",
                (leader.id,),
            )
            await conn.commit()

    # Create manager session (DB-first)

    session = await db_ops.create_session(
        conn,
        project_id=project_id,
        session_type="manager",
        name=name,
        provider=provider,
        scope_type="project",
        scope_id=project_id,
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
        # Resolve API base URL from settings so agents use the real running address,
        # not a hardcoded default.  Falls back to ATC_API_URL env var / load_settings()
        # via _resolve_api_base_url if settings are unavailable here.
        import os as _os

        _api_url = _os.environ.get("ATC_API_URL", "")
        if not _api_url:
            try:
                from atc.config import load_settings as _ls

                _s = _ls()
                _api_url = f"http://{_s.server.host}:{_s.server.port}"
            except Exception:
                pass
        spec = _build_manager_deploy_spec(
            leader_id=leader.id,
            project_name=ctx.get("project_name") or (project.name if project else ""),
            goal=goal or leader.goal or "",
            project_id=project_id,
            session_id=session.id,
            repo_path=ctx.get("repo_path") or (project.repo_path if project else None),
            github_repo=ctx.get("github_repo") or (project.github_repo if project else None),
            context_entries=ctx.get("context_entries"),
            api_base_url=_api_url,
        )
        deployed = deploy_manager_files(spec)
        logger.info(
            "Deployed manager config for leader %s (session %s) → %s",
            leader.id,
            session.id,
            deployed.root,
        )

        # Copy provider-neutral instructions (and provider compatibility files)
        # into repo_path so the selected CLI agent picks up the leader role.
        if spec.repo_path and Path(spec.repo_path) != deployed.root:
            import shutil as _shutil

            dest = Path(spec.repo_path)
            dest.mkdir(parents=True, exist_ok=True)
            for instructions_src in (deployed.agents_md_path, deployed.claude_md_path):
                if instructions_src.exists():
                    _shutil.copy2(instructions_src, dest / instructions_src.name)
                    logger.info(
                        "Copied leader instructions %s to repo_path: %s",
                        instructions_src.name,
                        dest,
                    )
                else:
                    logger.warning(
                        "%s not found at %s, skipping copy", instructions_src.name, deployed.root
                    )
            claude_src = deployed.root / ".claude"
            if claude_src.exists():
                _shutil.copytree(claude_src, dest / ".claude", dirs_exist_ok=True)

        # Use repo_path if available so Claude Code starts in the actual repo;
        # fall back to staging dir so it finds the deployed instructions and hooks.
        # Ensure the directory exists — tmux silently falls back to $HOME if the
        # working_dir does not exist, causing Claude Code to start in the wrong place.
        if spec.repo_path:
            import os as _os

            _os.makedirs(spec.repo_path, exist_ok=True)
            logger.info("Ensured repo_path exists: %s", spec.repo_path)
        working_dir = spec.repo_path or str(deployed.root)

        # Provider workspace prep via the new runtime service boundary.
        try:
            from atc.runtime.models import RoleKind, StartRoleRequest
            from atc.runtime.service import RuntimeService

            _cmp = deployed.instructions_md_path
            _ctx = str(_cmp) if _cmp.exists() else None
            await RuntimeService().prepare_workspace(
                StartRoleRequest(
                    session_id=session.id,
                    provider_name=provider,
                    role=RoleKind.LEADER,
                    project_id=project_id,
                    working_dir=working_dir,
                    context_ref=_ctx,
                )
            )
        except Exception as _prep_exc:
            logger.debug(
                "runtime.prepare_workspace skipped for leader %s: %s",
                session.id,
                _prep_exc,
            )

        tmux_session, pane_id = await _spawn_provider_session(
            conn,
            session.id,
            project_id=project_id,
            session_type="manager",
            working_dir=working_dir,
            context_file=deployed.instructions_md_path
            if deployed.instructions_md_path.exists()
            else None,
        )
        await db_ops.update_session_tmux(conn, session.id, tmux_session, pane_id)

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

    await RuntimeService().stop_session_record(
        session, StopRoleRequest(reason="leader_stop", graceful=True)
    )

    # Transition session to idle after provider-neutral stop.
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
    metadata: dict[str, object] | None = None,
) -> RuntimeDeliveryResult:
    """Send a message to the Leader through the runtime boundary."""
    leader = await db_ops.get_leader_by_project(conn, project_id)
    if leader is None or leader.session_id is None:
        raise ValueError(f"No active leader for project {project_id}")

    session = await db_ops.get_session(conn, leader.session_id)
    if session is None:
        raise ValueError("Leader session not found")
    current = SessionStatus(session.status)

    # Reject sends to sessions that are clearly not running.
    if current in (SessionStatus.ERROR, SessionStatus.DISCONNECTED):
        raise ValueError(f"Leader session is {current.value} — stop and restart the leader")

    if current in (SessionStatus.IDLE, SessionStatus.WAITING):
        await transition(session.id, current, SessionStatus.WORKING, event_bus)
        await db_ops.update_session_status(conn, session.id, SessionStatus.WORKING.value)

    runtime = RuntimeService()
    handle = runtime.handle_from_session_record(session)
    request_metadata: dict[str, object] = {"source": "leader_message"}
    if metadata:
        request_metadata.update(metadata)
    request = InstructionRequest(
        session_id=session.id,
        message=message,
        metadata=request_metadata,
    )
    try:
        result = await runtime.assign_project_to_leader(handle, request)
    finally:
        await _persist_delivery_trace_events(
            conn,
            session_id=session.id,
            project_id=session.project_id,
            trace_events=request.metadata.get("delivery_trace_events", []),
        )
    return result

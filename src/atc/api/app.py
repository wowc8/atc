"""FastAPI application factory with lifespan management."""

from __future__ import annotations

import asyncio
import os
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse

from atc import __version__
from atc.api.ws.hub import WsHub
from atc.config import Settings, load_settings
from atc.core.errors import ATCError
from atc.core.events import EventBus
from atc.core.sentry import init_sentry
from atc.state.db import run_migrations
from atc.terminal.pty_stream import PtyStreamPool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — startup and shutdown sequence."""
    settings: Settings = app.state.settings
    db_path = settings.database.path
    import time as _time

    logger.info("ATC v%s starting up (db=%s)", __version__, db_path)
    app.state.startup_at = _time.monotonic()

    # 1. Run DB migrations
    await run_migrations(db_path)

    # 2. Start event bus
    event_bus = EventBus()
    await event_bus.start()
    app.state.event_bus = event_bus

    # 3. Open a persistent DB connection for the app
    import aiosqlite

    db = await aiosqlite.connect(db_path, timeout=30.0)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute("PRAGMA busy_timeout=30000")
    db.row_factory = aiosqlite.Row
    app.state.db = db

    # 4. Start WebSocket hub
    ws_hub = WsHub()
    app.state.ws_hub = ws_hub

    # Wire failure log broadcasting
    from atc.core.failure_log import set_ws_hub

    set_ws_hub(ws_hub)

    # Wire app event broadcasting
    from atc.core import app_events as _app_events_mod

    _app_events_mod.set_ws_hub(ws_hub)

    # 4b. Start Tower controller
    from atc.tower.controller import TowerController

    tower_controller = TowerController(db, event_bus, ws_hub=ws_hub)
    app.state.tower_controller = tower_controller

    # 5. Start PTY stream pool and wire to WsHub
    pty_pool = PtyStreamPool(event_bus)
    await pty_pool.start()
    app.state.pty_pool = pty_pool

    # Forward pty_output events to WebSocket clients
    async def _on_pty_output(data: dict[str, Any]) -> None:
        session_id = data.get("session_id", "")
        raw = data.get("data", b"")
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        await ws_hub.broadcast(f"terminal:{session_id}", text)

    event_bus.subscribe("pty_output", _on_pty_output)

    # Forward terminal input from WebSocket clients to PTY
    async def _on_ws_input(channel: str, data: str) -> None:
        # channel is "terminal:{session_id}"
        session_id = channel.removeprefix("terminal:")
        try:
            await pty_pool.send_keys(session_id, data)
        except ValueError:
            logger.debug("No PTY reader for session %s (input dropped)", session_id)

    ws_hub.on_input(_on_ws_input)

    # Forward terminal resize events from WebSocket clients to PTY
    async def _on_ws_resize(channel: str, cols: int, rows: int) -> None:
        session_id = channel.removeprefix("terminal:")
        try:
            await pty_pool.resize_pane(session_id, cols, rows)
        except (ValueError, RuntimeError):
            logger.debug("Resize failed for session %s (cols=%d, rows=%d)", session_id, cols, rows)

    ws_hub.on_resize(_on_ws_resize)

    # Send initial terminal content when a client subscribes to a terminal channel.
    # This captures the current tmux pane content so the terminal isn't blank on load.
    async def _on_terminal_subscribe(ws: Any, channel: str) -> None:
        session_id = channel.removeprefix("terminal:")
        if not session_id:
            return
        try:
            content = await pty_pool.capture_pane(session_id)
            if content and content.strip():
                await ws_hub.send_to(ws, channel, content)
        except (ValueError, RuntimeError):
            # No PTY reader or capture failed — skip silently
            pass

    ws_hub.on_subscribe(_on_terminal_subscribe)

    # Auto-start PTY readers when sessions are created with a tmux pane
    async def _on_session_created(data: dict[str, Any]) -> None:
        session_id = data.get("session_id", "")
        if not session_id:
            return
        # Broadcast new session to the frontend so the Aces panel updates
        # immediately without waiting for the next fetchAll poll.
        from atc.state import db as db_ops

        session = await db_ops.get_session(db, session_id)
        if session:
            await ws_hub.broadcast(
                "state",
                {
                    "session_created": True,
                    "session": {
                        "id": session.id,
                        "project_id": session.project_id,
                        "session_type": session.session_type,
                        "name": session.name,
                        "status": session.status,
                        "task_id": session.task_id,
                        "tmux_session": session.tmux_session,
                        "tmux_pane": session.tmux_pane,
                        "created_at": session.created_at,
                        "updated_at": session.updated_at,
                    },
                },
            )

    async def _on_session_status_changed(data: dict[str, Any]) -> None:
        session_id = data.get("session_id", "")
        new_status = data.get("new_status", "")
        if not session_id:
            return

        # When a session transitions to idle (pane just spawned), start the PTY reader
        if new_status == "idle" and pty_pool.get_reader(session_id) is None:
            from atc.state import db as db_ops

            session = await db_ops.get_session(db, session_id)
            if session and session.tmux_pane:
                logger.info(
                    "Auto-starting PTY reader for session %s (pane %s)",
                    session_id,
                    session.tmux_pane,
                )
                try:
                    await pty_pool.add_session(session_id, session.tmux_pane)
                except Exception as _pty_err:
                    # Pane may have died between spawn and PTY reader start (e.g. claude
                    # binary not found). Log and continue — don't crash the event handler.
                    logger.warning(
                        "PTY reader failed for session %s pane %s: %s",
                        session_id,
                        session.tmux_pane,
                        _pty_err,
                    )

        # Broadcast status changes on the state channel for AppContext
        await ws_hub.broadcast(
            "state",
            {
                "sessions_updated": True,
                "session_id": session_id,
                "new_status": new_status,
            },
        )

    async def _on_session_destroyed(data: dict[str, Any]) -> None:
        session_id = data.get("session_id", "")
        if session_id:
            await pty_pool.remove_session(session_id)

    event_bus.subscribe("session_created", _on_session_created)
    event_bus.subscribe("session_status_changed", _on_session_status_changed)
    event_bus.subscribe("session_destroyed", _on_session_destroyed)

    # 6. Start heartbeat monitor
    from atc.core.heartbeat import HeartbeatMonitor

    hb_cfg = settings.heartbeat
    heartbeat_monitor = HeartbeatMonitor(
        db,
        event_bus,
        ws_hub=ws_hub,
        check_interval=hb_cfg.check_interval_seconds,
        stale_threshold=hb_cfg.stale_threshold_seconds,
    )
    if hb_cfg.enabled:
        await heartbeat_monitor.start()
    app.state.heartbeat_monitor = heartbeat_monitor

    # Auto-register heartbeat when sessions are created
    async def _on_session_created_hb(data: dict[str, Any]) -> None:
        session_id = data.get("session_id", "")
        if session_id:
            await heartbeat_monitor.register(session_id)

    # Auto-deregister heartbeat when sessions are destroyed (clean shutdown)
    async def _on_session_destroyed_hb(data: dict[str, Any]) -> None:
        session_id = data.get("session_id", "")
        if session_id:
            await heartbeat_monitor.deregister(session_id)

    event_bus.subscribe("session_created", _on_session_created_hb)
    event_bus.subscribe("session_destroyed", _on_session_destroyed_hb)

    # Wire WebSocket heartbeat piggyback
    async def _on_ws_heartbeat(session_id: str) -> None:
        await heartbeat_monitor.handle_heartbeat(session_id)

    ws_hub.on_heartbeat(_on_ws_heartbeat)

    # 6b. Run startup cleanup — remove orphaned sessions and staging dirs
    from atc.core.cleanup import run_startup_cleanup

    try:
        cleanup_totals = await run_startup_cleanup(db)
        logger.info("Startup cleanup: %s", cleanup_totals)
    except Exception:
        logger.exception("Startup cleanup failed — continuing")

    # 6c. Run startup smoke test — validates pane spawn + instruction delivery
    import time as _time

    app.state.startup_at = _time.monotonic()
    from atc.core.health import HealthResult as _HealthResult, run_startup_smoke_test

    try:
        health = await asyncio.wait_for(run_startup_smoke_test(), timeout=15.0)
    except asyncio.TimeoutError:
        health = _HealthResult(ok=False, message="smoke test timed out after 15s", duration_ms=15000.0)
    except Exception as _exc:
        health = _HealthResult(ok=False, message=f"smoke test error: {_exc}", duration_ms=0.0)

    app.state.health = health
    if health.ok:
        logger.info("Startup smoke test passed (%.0fms)", health.duration_ms)
    else:
        logger.warning("Startup smoke test failed: %s (%.0fms)", health.message, health.duration_ms)

    # 7. Reconnect sessions that were active at last shutdown
    from atc.session.reconnect import reconnect_all
    from atc.state import db as db_ops

    try:
        results = await asyncio.wait_for(
            reconnect_all(db, event_bus=event_bus),
            timeout=20.0,
        )
        if results:
            ok = sum(1 for v in results.values() if v)
            logger.info("Reconnected %d/%d sessions on startup", ok, len(results))
    except asyncio.TimeoutError:
        logger.warning("Session reconnection timed out after 20s — continuing startup")
    except Exception:
        logger.exception("Session reconnection failed on startup")

    # 7b. Start PTY readers for all reconnected sessions that have live panes
    # (the event-driven auto-start may have missed sessions that were already idle)
    from atc.session.ace import _pane_is_alive

    try:
        all_sessions = await db_ops.list_active_sessions(db)
        for sess in all_sessions:
            if sess.tmux_pane and pty_pool.get_reader(sess.id) is None:
                # Verify the pane is actually alive before attaching a PTY reader.
                # A stale pane ID (e.g. %154 from a previous run) will cause
                # "tmux pipe-pane failed: can't find pane" if we blindly attach.
                if not await _pane_is_alive(sess.tmux_pane):
                    logger.warning(
                        "Skipping PTY reader for session %s — pane %s is dead; "
                        "marking session disconnected",
                        sess.id,
                        sess.tmux_pane,
                    )
                    await db_ops.update_session_status(db, sess.id, "disconnected")
                    continue
                logger.info(
                    "Starting PTY reader for reconnected session %s (pane %s)",
                    sess.id,
                    sess.tmux_pane,
                )
                await pty_pool.add_session(sess.id, sess.tmux_pane)
    except Exception:
        logger.exception("Failed to start PTY readers for reconnected sessions")

    # 8. Restore TowerController state from DB so existing tower sessions
    # survive server restarts without the frontend needing to re-create them.
    from atc.tower.controller import TowerState

    try:
        # include_system=True so the 'Tower Workspace' sentinel project is
        # included — the tower session lives there and would be missed otherwise.
        projects = await db_ops.list_projects(db, include_system=True)
        restored = False
        for proj in projects:
            if restored:
                break
            tower_sessions = await db_ops.list_sessions(
                db, project_id=proj.id, session_type="tower"
            )
            for ts in tower_sessions:
                if ts.status not in ("error", "disconnected") and ts.tmux_pane:
                    # Verify the tmux pane is actually alive — don't restore
                    # state for dead panes (let frontend auto-start instead).
                    if not await _pane_is_alive(ts.tmux_pane):
                        logger.warning(
                            "Tower session %s has dead pane %s — skipping restore",
                            ts.id,
                            ts.tmux_pane,
                        )
                        await db_ops.update_session_status(
                            db, ts.id, "disconnected"
                        )
                        continue

                    tower_controller._current_project_id = proj.id
                    tower_controller._current_session_id = ts.id
                    # Also check for a leader session
                    leader = await db_ops.get_leader_by_project(db, proj.id)
                    if leader and leader.session_id:
                        leader_session = await db_ops.get_session(db, leader.session_id)
                        if leader_session and leader_session.status not in (
                            "error",
                            "disconnected",
                        ):
                            # Verify leader pane is alive too
                            if leader_session.tmux_pane and await _pane_is_alive(
                                leader_session.tmux_pane
                            ):
                                tower_controller._leader_session_id = leader.session_id
                                tower_controller._current_goal = leader.goal
                                logger.info(
                                    "Restored leader session=%s goal=%s",
                                    leader.session_id,
                                    leader.goal,
                                )
                            else:
                                logger.warning(
                                    "Leader session %s has dead pane — clearing from leader row",
                                    leader.session_id,
                                )
                                await db.execute(
                                    "UPDATE leaders SET session_id = NULL, status = 'idle',"
                                    " updated_at = datetime('now') WHERE id = ?",
                                    (leader.id,),
                                )
                                await db.commit()
                    # Only restore as MANAGING if there is an active goal;
                    # a live pane with no goal means the Tower is effectively
                    # idle and the frontend should not show an active terminal.
                    if tower_controller._current_goal:
                        tower_controller._state = TowerState.MANAGING
                        logger.info(
                            "Restored TowerController state: MANAGING project=%s session=%s",
                            proj.id,
                            ts.id,
                        )
                    else:
                        tower_controller._state = TowerState.IDLE
                        logger.info(
                            "Restored TowerController state: IDLE (no active goal)"
                            " project=%s session=%s",
                            proj.id,
                            ts.id,
                        )

                    # Start PTY reader for the restored tower pane so the frontend
                    # terminal is populated on load (without this the subscribe handler
                    # throws ValueError: No active reader and returns nothing).
                    try:
                        await pty_pool.add_session(ts.id, ts.tmux_pane)
                        logger.info(
                            "Started PTY reader for restored tower session %s (pane %s)",
                            ts.id,
                            ts.tmux_pane,
                        )
                    except Exception as _pty_err:
                        logger.warning(
                            "Could not start PTY reader for restored tower session %s: %s",
                            ts.id,
                            _pty_err,
                        )

                    restored = True
                    break
    except Exception:
        logger.exception("Failed to restore TowerController state from DB")

    # 8b. Auto-start Tower if no session was restored and state is still idle.
    # Skip if a session was already restored (even as IDLE) — the pane is alive
    # and the frontend will show it correctly. Auto-starting on top of a restored
    # session would spawn a duplicate pane with no project context.
    # Runs in background to avoid blocking the lifespan for 30-60s.
    async def _auto_start_tower() -> None:
        try:
            if tower_controller._current_session_id:
                logger.info(
                    "Tower has restored session %s — skipping auto-start",
                    tower_controller._current_session_id,
                )
                return

            if tower_controller._state != TowerState.IDLE:
                return

            # Only auto-start if there are user-created projects to work with.
            # On a fresh install there are no projects yet — starting Tower with
            # project_id=None produces a "● Managing" header with no context,
            # which is confusing and blocks first-run UX.
            from atc.state import db as db_ops
            user_projects = await db_ops.list_projects(db, include_system=False)
            if not user_projects:
                logger.info("Tower idle with no user projects — skipping auto-start (first run or cleared DB)")
                return

            logger.info("Tower is idle with no session — auto-starting session")
            await tower_controller.start_session()
            logger.info(
                "Tower auto-started: session=%s", tower_controller._current_session_id
            )
        except Exception:
            logger.exception("Tower auto-start failed — Tower will start in idle state")

    asyncio.create_task(_auto_start_tower())

    # 9. Start resource monitor
    from atc.tracking.resources import ResourceMonitor

    resource_monitor = ResourceMonitor(db, event_bus, ws_hub=ws_hub)
    await resource_monitor.start()
    app.state.resource_monitor = resource_monitor

    # 10. Start cost tracker
    from atc.tracking.costs import CostTracker

    cost_tracker = CostTracker(db, event_bus, ws_hub=ws_hub)
    await cost_tracker.start()
    app.state.cost_tracker = cost_tracker

    # 11. Start GitHub tracker
    from atc.tracking.github import GitHubTracker

    github_tracker = GitHubTracker(db, event_bus, ws_hub=ws_hub)
    await github_tracker.start()
    app.state.github_tracker = github_tracker

    # 12. Start budget enforcer
    from atc.tracking.budget import BudgetEnforcer

    budget_enforcer = BudgetEnforcer(db, event_bus, ws_hub=ws_hub)
    await budget_enforcer.start()
    app.state.budget_enforcer = budget_enforcer

    # 13. Start memory consolidation cron
    from atc.memory.cron import MemoryCron

    memory_cron = MemoryCron(db_path, event_bus, ws_hub=ws_hub)
    await memory_cron.start()
    app.state.memory_cron = memory_cron

    # 14. Start backup service
    from pathlib import Path as _Path

    from atc.backup.service import BackupService

    backup_cfg = settings.backup
    backup_service = BackupService(
        db_path=_Path(db_path),
        config_path=_Path("config.yaml"),
        backup_dir=_Path(backup_cfg.local_backup_dir).expanduser(),
        keep_last_n=backup_cfg.keep_last_n,
    )
    app.state.backup_service = backup_service
    if backup_cfg.auto_backup_enabled:
        await backup_service.schedule_auto_backup(
            interval_hours=backup_cfg.auto_backup_interval_hours
        )

    # Log auth mode warning
    from atc.agents.auth import get_auth_mode, claude_credentials_exist

    _auth_mode = get_auth_mode()
    if _auth_mode == "oauth":
        _key = os.environ.get("ATC_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        if _key:
            logger.warning(
                "Running in OAuth mode — cost tracking disabled, concurrent sessions may conflict. "
                "Set ATC_ANTHROPIC_API_KEY for full functionality."
            )
        else:
            # No env var — using Claude's own stored credentials (best case for local dev)
            logger.info(
                "Auth: Claude Code credentials found at ~/.claude/credentials.json — "
                "agents will run as the logged-in Claude user. "
                "Set ATC_ANTHROPIC_API_KEY for a dedicated API key."
            )
    elif _auth_mode == "api_key":
        logger.info("Auth: API key configured (ATC_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY)")
    elif _auth_mode == "none":
        logger.warning(
            "⚠️  No auth configured. Claude Code does not appear to be logged in "
            "(~/.claude/credentials.json not found) and no API key env vars are set. "
            "Run 'claude login' or set ATC_ANTHROPIC_API_KEY. "
            "Agent terminals will show 'Not logged in' and produce blank terminals."
        )

    # Resolve the claude binary path at startup to handle nvm / non-standard installs.
    # On macOS with nvm, tmux panes spawn without sourcing shell RC files, so the
    # bare "claude" command is not in PATH.  We resolve it once here and update the
    # settings so every call to get_launch_command() picks up the absolute path.
    _resolve_claude_binary(settings)

    logger.info("ATC startup complete")
    yield

    # Shutdown
    logger.info("ATC shutting down")
    await backup_service.stop_auto_backup()
    await memory_cron.stop()
    await budget_enforcer.stop()
    await github_tracker.stop()
    await cost_tracker.stop()
    await resource_monitor.stop()
    await heartbeat_monitor.stop()
    await pty_pool.stop()
    await event_bus.stop()
    await db.close()


def _resolve_claude_binary(settings: "Settings") -> None:
    """Resolve the absolute path to the claude binary and update settings.

    On macOS with nvm, tmux panes spawn without sourcing shell RC files, so the
    bare claude command is often not in PATH.  This function probes common
    install locations at startup and updates settings.agent_provider.claude_command
    to the absolute path so all subsequent spawns find the binary reliably.
    """
    import glob
    import shutil
    from pathlib import Path as _Path

    current_cmd = settings.agent_provider.claude_command
    # If the command contains a space (e.g. "claude --flags"), check just the binary part
    binary_name = current_cmd.split()[0] if current_cmd else "claude"

    # 1. Check if it's already resolvable via PATH
    resolved = shutil.which(binary_name)
    if resolved:
        # Already in PATH — ensure settings uses the full command unchanged
        return

    # 2. Probe common locations
    candidates: list[str] = []
    home = str(_Path.home())
    # nvm installs (sorted so we get the newest node version last → first after reverse)
    candidates.extend(sorted(glob.glob(f"{home}/.nvm/versions/node/*/bin/claude"), reverse=True))
    candidates.append(f"{home}/.npm-global/bin/claude")
    candidates.append("/usr/local/bin/claude")
    candidates.append(f"{home}/.volta/bin/claude")
    candidates.append(f"{home}/.fnm/current/bin/claude")

    for candidate in candidates:
        if _Path(candidate).is_file():
            # Reconstruct the full command with the resolved binary path
            rest = current_cmd[len(binary_name):].strip()
            new_cmd = candidate if not rest else f"{candidate} {rest}"
            logger.warning(
                "claude binary not found in PATH; resolved to %s — updating settings",
                candidate,
            )
            # Settings is a pydantic model; agent_provider is a nested model.
            # We patch the attribute directly since Settings may be frozen at the top
            # level but agent_provider sub-model fields are mutable.
            try:
                settings.agent_provider.claude_command = new_cmd
            except Exception:
                # If the model is frozen, rebuild agent_provider
                from atc.config import AgentProviderConfig
                object.__setattr__(
                    settings,
                    "agent_provider",
                    settings.agent_provider.model_copy(update={"claude_command": new_cmd}),
                )
            # Also update the factory registry so get_launch_command() uses the full path
            from atc.agents import factory as _factory
            _factory._LAUNCH_COMMANDS["claude_code"] = new_cmd
            return

    logger.warning(
        "Could not resolve claude binary from PATH or common locations. "
        "Tmux panes may fail to launch if nvm is not sourced."
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if settings is None:
        settings = load_settings()

    # Initialise Sentry before creating the app so the FastAPI integration hooks in
    init_sentry(settings.sentry)

    app = FastAPI(
        title="ATC",
        version=__version__,
        description="Hierarchical AI orchestration platform",
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Publish the server's actual address as an environment variable so that
    # agent deploy helpers (deploy.py) and any child processes can discover it
    # dynamically instead of guessing from a hardcoded default.
    import os as _os
    _api_url = f"http://{settings.server.host}:{settings.server.port}"
    _os.environ.setdefault("ATC_API_URL", _api_url)

    # Register domain error handler — serializes ATCError subclasses to JSON
    @app.exception_handler(ATCError)
    async def _atc_error_handler(request: Request, exc: ATCError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    # Register routers
    from atc.api.routers import (
        aces,
        backup,
        context,
        failure_logs,
        feature_flags,
        heartbeat,
        leader,
        memory,
        projects,
        qa,
        system,
        task_graphs,
        tasks,
        tower,
        usage,
    )
    from atc.api.routers import settings as settings_router

    app.include_router(tower.router, prefix="/api/tower", tags=["tower"])
    app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
    app.include_router(tasks.router, prefix="/api", tags=["tasks"])
    app.include_router(task_graphs.router, prefix="/api", tags=["task_graphs"])
    app.include_router(leader.router, prefix="/api", tags=["leader"])
    app.include_router(aces.router, prefix="/api", tags=["aces"])
    app.include_router(usage.router, prefix="/api/usage", tags=["usage"])
    app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
    app.include_router(failure_logs.router, prefix="/api", tags=["failure_logs"])
    app.include_router(heartbeat.router, prefix="/api", tags=["heartbeat"])
    app.include_router(feature_flags.router, prefix="/api/feature-flags", tags=["feature_flags"])
    app.include_router(context.router, prefix="/api", tags=["context"])
    app.include_router(memory.router, prefix="/api/memory", tags=["memory"])
    app.include_router(backup.router, prefix="/api/backup", tags=["backup"])
    app.include_router(qa.router, prefix="/api/qa", tags=["qa"])
    app.include_router(system.router, prefix="/api/system", tags=["system"])

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, object]:
        import time as _time

        startup_at: float | None = getattr(request.app.state, "startup_at", None)
        startup_duration_ms = (
            (_time.monotonic() - startup_at) * 1000 if startup_at is not None else None
        )
        smoke: object = getattr(request.app.state, "health", None)
        if smoke is None:
            return {
                "status": "ok",
                "message": "startup in progress",
                "duration_ms": 0.0,
                "startup_duration_ms": startup_duration_ms,
                "version": __version__,
            }
        from atc.agents.auth import get_auth_mode
        from atc.core.health import HealthResult as _HR
        assert isinstance(smoke, _HR)
        return {
            "status": "ok" if smoke.ok else "degraded",
            "message": smoke.message,
            "duration_ms": smoke.duration_ms,
            "startup_duration_ms": startup_duration_ms,
            "version": __version__,
            "auth_mode": get_auth_mode(),
        }

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        hub: WsHub = app.state.ws_hub
        await hub.handle(ws)

    return app


def main() -> None:
    """Entry point for the ATC server."""
    import socket

    settings = load_settings()
    logging.basicConfig(level=getattr(logging, settings.logging.level))

    # Check if port is already in use before starting
    host = settings.server.host
    port = settings.server.port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except OSError as exc:
        logger.error(
            "Port %d is already in use on %s: %s. "
            "Kill the stale process (lsof -i :%d) or choose a different port.",
            port,
            host,
            exc,
            port,
        )
        raise SystemExit(1) from exc
    finally:
        sock.close()

    app = create_app(settings)
    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=settings.server.reload,
    )

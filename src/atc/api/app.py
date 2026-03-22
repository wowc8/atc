"""FastAPI application factory with lifespan management."""

from __future__ import annotations

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
    logger.info("ATC v%s starting up (db=%s)", __version__, db_path)

    # 1. Run DB migrations
    await run_migrations(db_path)

    # 2. Start event bus
    event_bus = EventBus()
    await event_bus.start()
    app.state.event_bus = event_bus

    # 3. Open a persistent DB connection for the app
    import aiosqlite

    db = await aiosqlite.connect(db_path)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
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
        # Look up the session to get its tmux_pane (it may not be set yet at
        # creation time; the pane is spawned right after the event).  We
        # subscribe to status changes instead to catch the pane_id.
        pass

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
                await pty_pool.add_session(session_id, session.tmux_pane)

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

    # 7. Reconnect sessions that were active at last shutdown
    from atc.session.reconnect import reconnect_all
    from atc.state import db as db_ops

    try:
        results = await reconnect_all(db, event_bus=event_bus)
        if results:
            ok = sum(1 for v in results.values() if v)
            logger.info("Reconnected %d/%d sessions on startup", ok, len(results))
    except Exception:
        logger.exception("Session reconnection failed on startup")

    # 7b. Start PTY readers for all reconnected sessions that have live panes
    # (the event-driven auto-start may have missed sessions that were already idle)
    try:
        all_sessions = await db_ops.list_active_sessions(db)
        for sess in all_sessions:
            if sess.tmux_pane and pty_pool.get_reader(sess.id) is None:
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
    from atc.session.ace import _pane_is_alive
    from atc.tower.controller import TowerState

    try:
        projects = await db_ops.list_projects(db)
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
                    tower_controller._state = TowerState.MANAGING
                    logger.info(
                        "Restored TowerController state: project=%s session=%s",
                        proj.id,
                        ts.id,
                    )
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
                    restored = True
                    break
    except Exception:
        logger.exception("Failed to restore TowerController state from DB")

    # 8b. Auto-start Tower if it's still idle after restore attempt.
    # Tower should always be running when the app starts — no manual click required.
    try:
        if tower_controller._state == TowerState.IDLE:
            logger.info("Tower is idle after startup — auto-starting session")
            await tower_controller.start_session()
            logger.info("Tower auto-started: session=%s", tower_controller._current_session_id)
    except Exception:
        logger.exception("Tower auto-start failed — Tower will start in idle state")

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

    memory_cron = MemoryCron(db, event_bus, ws_hub=ws_hub)
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

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        return {"ok": True, "version": __version__}

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

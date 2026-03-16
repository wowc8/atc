"""FastAPI application factory with lifespan management."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI

from atc import __version__
from atc.config import Settings, load_settings
from atc.core.events import EventBus
from atc.state.db import get_connection, run_migrations

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

    # 4. Reconnect sessions that were active at last shutdown
    from atc.session.reconnect import reconnect_all

    try:
        results = await reconnect_all(db, event_bus=event_bus)
        if results:
            ok = sum(1 for v in results.values() if v)
            logger.info("Reconnected %d/%d sessions on startup", ok, len(results))
    except Exception:
        logger.exception("Session reconnection failed on startup")

    logger.info("ATC startup complete")
    yield

    # Shutdown
    logger.info("ATC shutting down")
    await event_bus.stop()
    await db.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if settings is None:
        settings = load_settings()

    app = FastAPI(
        title="ATC",
        version=__version__,
        description="Hierarchical AI orchestration platform",
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Register routers
    from atc.api.routers import aces, projects, settings as settings_router, tasks, tower, usage

    app.include_router(tower.router, prefix="/api/tower", tags=["tower"])
    app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
    app.include_router(tasks.router, prefix="/api", tags=["tasks"])
    app.include_router(aces.router, prefix="/api", tags=["aces"])
    app.include_router(usage.router, prefix="/api/usage", tags=["usage"])
    app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        return {"ok": True, "version": __version__}

    return app


def main() -> None:
    """Entry point for the ATC server."""
    settings = load_settings()
    logging.basicConfig(level=getattr(logging, settings.logging.level))
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.server.host,
        port=settings.server.port,
        reload=settings.server.reload,
    )

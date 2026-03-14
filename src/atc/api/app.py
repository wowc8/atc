"""FastAPI application factory with lifespan management."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI

from atc import __version__
from atc.config import Settings, load_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — startup and shutdown sequence."""
    settings: Settings = app.state.settings
    logger.info("ATC v%s starting up", __version__)

    # 1. Run DB migrations
    # TODO: await run_migrations(settings.database)

    # 2. Start event bus
    # TODO: app.state.event_bus = EventBus()

    # 3. Start state manager (queued DB writes)
    # TODO: app.state.state_manager = StateManager(...)

    # 4. Start PtyStreamPool
    # TODO: app.state.pty_pool = PtyStreamPool(...)

    # 5. Start Tower controller loop
    # TODO: if settings.tower.enabled: ...

    # 6. Start resource monitor (psutil, 5s interval)
    # TODO: if settings.resource_monitor.enabled: ...

    # 7. Start GitHub tracker (60s poll interval)
    # TODO: app.state.github_tracker = GitHubTracker(...)

    # 8. Start budget enforcer watcher
    # TODO: app.state.budget_enforcer = BudgetEnforcer(...)

    # 9. Reconnect sessions that were active at last shutdown
    # TODO: await reconnect_active_sessions()

    logger.info("ATC startup complete")
    yield
    # Shutdown in reverse order; drain queues before closing DB
    logger.info("ATC shutting down")


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

from __future__ import annotations

import asyncio
import os

from atc.config import load_settings
from atc.mcp.server import MCPServer, MCPStdioServer
from atc.orchestration.service import OrchestrationService
from atc.state.db import get_connection, run_migrations


async def _run() -> None:
    settings = load_settings()
    db_path = os.environ.get('ATC_DB_PATH', settings.database.path)
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        service = OrchestrationService(db)
        stdio = MCPStdioServer(MCPServer(service))
        while await stdio.run_once():
            pass


def main() -> None:
    asyncio.run(_run())

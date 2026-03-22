"""System resource monitor — samples CPU/RAM per project via psutil.

Every ``interval`` seconds, the monitor maps active tmux pane PIDs to
project_ids, sums CPU/RAM for each project's process tree, writes
``usage_events`` rows (event_type="cpu" and "ram") to the database,
and broadcasts snapshots on the ``resources`` WebSocket channel.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

    from atc.api.ws.hub import WsHub
    from atc.core.events import EventBus

logger = logging.getLogger(__name__)


async def _get_pane_pid(tmux_pane: str) -> int | None:
    """Return the PID for a tmux pane, or None on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "display-message",
            "-p",
            "-t",
            tmux_pane,
            "#{pane_pid}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0 and stdout.strip():
            return int(stdout.strip())
    except (OSError, ValueError):
        pass
    return None


def _process_tree_stats(pid: int) -> tuple[float, float]:
    """Return (cpu_pct, ram_mb) for a process and all its descendants."""
    try:
        import psutil

        parent = psutil.Process(pid)
        procs = [parent, *parent.children(recursive=True)]
        cpu = sum(p.cpu_percent(interval=None) for p in procs if p.is_running())
        ram = sum(
            p.memory_info().rss / (1024 * 1024)
            for p in procs
            if p.is_running()
        )
        return cpu, ram
    except Exception:
        return 0.0, 0.0


class ResourceMonitor:
    """Periodically samples CPU/RAM for each project's active sessions."""

    interval = 5.0

    def __init__(
        self,
        db: aiosqlite.Connection,
        event_bus: EventBus,
        *,
        ws_hub: WsHub | None = None,
        interval: float | None = None,
    ) -> None:
        self._db = db
        self._event_bus = event_bus
        self._ws_hub = ws_hub
        if interval is not None:
            self.interval = interval
        self._task: asyncio.Task[None] | None = None

        # Prime psutil CPU percent (first call always returns 0.0)
        try:
            import psutil

            psutil.cpu_percent(interval=None)
        except ImportError:
            logger.warning("psutil not available — resource monitoring disabled")

    async def start(self) -> None:
        """Start the background sampling loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._sample_loop())
        logger.info("ResourceMonitor started (interval=%.0fs)", self.interval)

    async def stop(self) -> None:
        """Stop the background sampling loop."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            logger.info("ResourceMonitor stopped")

    async def _sample_loop(self) -> None:
        while True:
            try:
                await self._sample_once()
            except Exception:
                logger.exception("ResourceMonitor sample failed")
            await asyncio.sleep(self.interval)

    async def _sample_once(self) -> None:
        """Sample resources for all active sessions and write usage_events."""
        import importlib.util

        if importlib.util.find_spec("psutil") is None:
            return

        # Load all active sessions with a tmux pane
        cursor = await self._db.execute(
            """SELECT id, project_id, tmux_pane FROM sessions
               WHERE status IN ('working', 'idle', 'connecting', 'waiting')
                 AND tmux_pane IS NOT NULL
               ORDER BY project_id""",
        )
        rows = await cursor.fetchall()
        if not rows:
            return

        # Map project_id → list of (session_id, tmux_pane)
        by_project: dict[str, list[tuple[str, str]]] = {}
        for row in rows:
            session_id = str(row[0])
            project_id = str(row[1])
            tmux_pane = str(row[2])
            by_project.setdefault(project_id, []).append((session_id, tmux_pane))

        now = datetime.now(UTC).isoformat()
        snapshot: list[dict[str, object]] = []

        for project_id, sessions in by_project.items():
            total_cpu = 0.0
            total_ram = 0.0

            for _session_id, tmux_pane in sessions:
                pid = await _get_pane_pid(tmux_pane)
                if pid is None:
                    continue
                cpu, ram = _process_tree_stats(pid)
                total_cpu += cpu
                total_ram += ram

            # Write one CPU event per project
            cpu_id = str(uuid.uuid4())
            await self._db.execute(
                """INSERT INTO usage_events
                   (id, project_id, event_type, cpu_pct, recorded_at)
                   VALUES (?, ?, 'cpu', ?, ?)""",
                (cpu_id, project_id, total_cpu, now),
            )
            # Write one RAM event per project
            ram_id = str(uuid.uuid4())
            await self._db.execute(
                """INSERT INTO usage_events
                   (id, project_id, event_type, ram_mb, recorded_at)
                   VALUES (?, ?, 'ram', ?, ?)""",
                (ram_id, project_id, total_ram, now),
            )

            snapshot.append(
                {
                    "project_id": project_id,
                    "cpu_pct": total_cpu,
                    "ram_mb": total_ram,
                    "timestamp": now,
                }
            )

        await self._db.commit()

        if self._ws_hub and snapshot:
            await self._ws_hub.broadcast("resources", {"snapshot": snapshot})

        logger.debug("ResourceMonitor: sampled %d projects", len(snapshot))

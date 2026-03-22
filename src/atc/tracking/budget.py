"""Budget enforcer — monitors project spend and pauses sessions when exceeded.

Every ``check_interval`` seconds the enforcer compares each project's
accumulated token/cost usage against its ``project_budgets`` limits,
transitions the status field (ok → warn → exceeded), writes notifications,
and pauses all sessions for the project when the budget is exceeded.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

    from atc.api.ws.hub import WsHub
    from atc.core.events import EventBus

logger = logging.getLogger(__name__)


class BudgetEnforcer:
    """Checks project budgets and enforces spend limits."""

    check_interval = 30.0

    def __init__(
        self,
        db: aiosqlite.Connection,
        event_bus: EventBus,
        *,
        ws_hub: WsHub | None = None,
        check_interval: float | None = None,
    ) -> None:
        self._db = db
        self._event_bus = event_bus
        self._ws_hub = ws_hub
        if check_interval is not None:
            self.check_interval = check_interval
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background check loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._check_loop())
        logger.info("BudgetEnforcer started (interval=%.0fs)", self.check_interval)

    async def stop(self) -> None:
        """Stop the background check loop."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            logger.info("BudgetEnforcer stopped")

    async def _check_loop(self) -> None:
        while True:
            try:
                await self._check_budgets()
            except Exception:
                logger.exception("BudgetEnforcer check failed")
            await asyncio.sleep(self.check_interval)

    async def _check_budgets(self) -> None:
        """Evaluate all project budgets and act on threshold crossings."""
        cursor = await self._db.execute("SELECT * FROM project_budgets")
        budgets = await cursor.fetchall()

        for row in budgets:
            project_id = str(row["project_id"])
            daily_token_limit: int | None = row["daily_token_limit"]
            monthly_cost_limit: float | None = row["monthly_cost_limit"]
            warn_threshold: float = float(row["warn_threshold"] or 0.8)
            current_status: str = str(row["current_status"] or "ok")

            try:
                new_status = await self._compute_status(
                    project_id,
                    daily_token_limit,
                    monthly_cost_limit,
                    warn_threshold,
                )
            except Exception:
                logger.exception("Failed to compute budget status for %s", project_id)
                continue

            if new_status == current_status:
                continue

            await self._transition_status(
                project_id,
                current_status,
                new_status,
                daily_token_limit,
                monthly_cost_limit,
                warn_threshold,
            )

    async def _compute_status(
        self,
        project_id: str,
        daily_token_limit: int | None,
        monthly_cost_limit: float | None,
        warn_threshold: float,
    ) -> str:
        """Return the budget status that should apply given current usage."""
        if daily_token_limit is None and monthly_cost_limit is None:
            return "ok"

        # Get today's token usage
        today = datetime.now(UTC).strftime("%Y-%m-%d")

        fractions: list[float] = []

        if daily_token_limit is not None and daily_token_limit > 0:
            cursor = await self._db.execute(
                """SELECT COALESCE(SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)), 0)
                   FROM usage_events
                   WHERE project_id = ?
                     AND event_type = 'ai_cost'
                     AND recorded_at >= ?""",
                (project_id, today),
            )
            row = await cursor.fetchone()
            today_tokens = int(row[0]) if row else 0
            fractions.append(today_tokens / daily_token_limit)

        if monthly_cost_limit is not None and monthly_cost_limit > 0:
            # Rolling 30-day window avoids month-end spend spike pattern.
            # Computed in Python so the format matches recorded_at (ISO-8601 with tz).
            rolling_start = (datetime.now(UTC) - timedelta(days=30)).isoformat()
            cursor = await self._db.execute(
                """SELECT COALESCE(SUM(COALESCE(cost_usd, 0)), 0)
                   FROM usage_events
                   WHERE project_id = ?
                     AND event_type = 'ai_cost'
                     AND recorded_at >= ?""",
                (project_id, rolling_start),
            )
            row = await cursor.fetchone()
            month_cost = float(row[0]) if row else 0.0
            fractions.append(month_cost / monthly_cost_limit)

        if not fractions:
            return "ok"

        max_fraction = max(fractions)
        if max_fraction >= 1.0:
            return "exceeded"
        if max_fraction >= warn_threshold:
            return "warn"
        return "ok"

    async def _transition_status(
        self,
        project_id: str,
        old_status: str,
        new_status: str,
        daily_token_limit: int | None,
        monthly_cost_limit: float | None,
        warn_threshold: float,
    ) -> None:
        """Apply a status transition and take required actions."""
        now = datetime.now(UTC).isoformat()

        # Update project_budgets table
        await self._db.execute(
            "UPDATE project_budgets SET current_status = ?, updated_at = ? WHERE project_id = ?",
            (new_status, now, project_id),
        )
        await self._db.commit()

        logger.info(
            "Budget status: project=%s %s → %s",
            project_id,
            old_status,
            new_status,
        )

        if new_status == "exceeded":
            await self._pause_project_sessions(project_id)
            await self._write_notification(
                project_id,
                "budget",
                "Budget exceeded for project — all sessions paused.",
            )
            await self._event_bus.publish(
                "budget_exceeded",
                {"project_id": project_id},
            )

        elif new_status == "warn":
            pct = int(warn_threshold * 100)
            await self._write_notification(
                project_id,
                "warning",
                f"Budget warning: {pct}% of limit reached.",
            )
            await self._event_bus.publish(
                "budget_warning",
                {"project_id": project_id},
            )

        elif new_status == "ok":
            await self._event_bus.publish(
                "budget_ok",
                {"project_id": project_id},
            )

        if self._ws_hub:
            await self._ws_hub.broadcast(
                f"budget:{project_id}",
                {
                    "project_id": project_id,
                    "old_status": old_status,
                    "new_status": new_status,
                    "updated_at": now,
                },
            )

    async def _pause_project_sessions(self, project_id: str) -> None:
        """Set all running sessions for a project to 'paused'."""
        now = datetime.now(UTC).isoformat()
        await self._db.execute(
            """UPDATE sessions
               SET status = 'paused', updated_at = ?
               WHERE project_id = ? AND status IN ('working', 'idle', 'connecting', 'waiting')""",
            (now, project_id),
        )
        await self._db.commit()
        logger.warning("Paused all active sessions for project %s (budget exceeded)", project_id)

    async def _write_notification(
        self,
        project_id: str,
        level: str,
        message: str,
    ) -> None:
        """Insert a notification row."""
        now = datetime.now(UTC).isoformat()
        notif_id = str(uuid.uuid4())
        await self._db.execute(
            """INSERT INTO notifications (id, project_id, level, message, read, created_at)
               VALUES (?, ?, ?, ?, 0, ?)""",
            (notif_id, project_id, level, message, now),
        )
        await self._db.commit()

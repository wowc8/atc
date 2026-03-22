"""AI cost tracker — polls ~/.claude/stats-cache.json for usage deltas.

Every ``poll_interval`` seconds the tracker reads the cumulative token counts
from Claude Code's stats cache, computes the delta against the previous
snapshot, attributes cost to the most recently active session, and writes a
``usage_events`` row to the database.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite

    from atc.api.ws.hub import WsHub
    from atc.core.events import EventBus

logger = logging.getLogger(__name__)

STATS_CACHE_PATH = Path.home() / ".claude" / "stats-cache.json"

MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-6":   {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6": {"input":  3.00, "output": 15.00},
    "claude-haiku-4-5":  {"input":  0.80, "output":  4.00},
}

_FALLBACK_MODEL = "claude-sonnet-4-6"


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return USD cost for token usage at published per-million-token rates.

    Falls back to Sonnet pricing for unknown models.
    """
    pricing = MODEL_PRICING.get(model, MODEL_PRICING[_FALLBACK_MODEL])
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


class CostTracker:
    """Polls ~/.claude/stats-cache.json and attributes cost deltas to sessions."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        event_bus: EventBus,
        *,
        ws_hub: WsHub | None = None,
        poll_interval: float = 30.0,
        stats_path: Path = STATS_CACHE_PATH,
    ) -> None:
        self._db = db
        self._event_bus = event_bus
        self._ws_hub = ws_hub
        self._poll_interval = poll_interval
        self._stats_path = stats_path
        self._last_snapshot: dict[str, Any] = {}
        self._task: asyncio.Task[None] | None = None
        # Sessions that report costs explicitly via atc-tower cost CLI.
        # Stats-cache polling is skipped for these to avoid double-counting.
        self._has_explicit_reporting: set[str] = set()

        # Subscribe to explicit cost reports from the atc-tower cost CLI
        self._event_bus.subscribe("cost_reported", self._on_cost_reported)

    async def start(self) -> None:
        """Start the background polling loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "CostTracker started (interval=%.0fs, path=%s)",
            self._poll_interval,
            self._stats_path,
        )

    async def stop(self) -> None:
        """Stop the background polling loop."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            logger.info("CostTracker stopped")

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._poll_once()
            except Exception:
                logger.exception("CostTracker poll failed")
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        """Read stats cache, compute delta, and write usage_events rows."""
        if not self._stats_path.exists():
            return

        try:
            raw = self._stats_path.read_text()
            data: dict[str, Any] = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read stats cache %s: %s", self._stats_path, exc)
            return

        delta = self._compute_delta(data, self._last_snapshot)
        self._last_snapshot = data

        if not delta:
            return

        session_id, project_id = await self._find_active_session()

        # Skip attribution if this session reports costs explicitly — avoid double-counting
        if session_id is not None and session_id in self._has_explicit_reporting:
            logger.debug(
                "Skipping stats-cache attribution for session %s (explicit reporting active)",
                session_id,
            )
            return

        for model, counts in delta.items():
            in_tok = counts.get("input_tokens", 0)
            out_tok = counts.get("output_tokens", 0)
            if in_tok == 0 and out_tok == 0:
                continue

            cost = calculate_cost(model, in_tok, out_tok)
            now = datetime.now(UTC).isoformat()
            event_id = str(uuid.uuid4())

            await self._db.execute(
                """INSERT INTO usage_events
                   (id, project_id, session_id, event_type, model,
                    input_tokens, output_tokens, cost_usd, recorded_at)
                   VALUES (?, ?, ?, 'ai_cost', ?, ?, ?, ?, ?)""",
                (event_id, project_id, session_id, model, in_tok, out_tok, cost, now),
            )
            await self._db.commit()

            if self._ws_hub:
                await self._ws_hub.broadcast(
                    "costs",
                    {
                        "event_id": event_id,
                        "model": model,
                        "input_tokens": in_tok,
                        "output_tokens": out_tok,
                        "cost_usd": cost,
                        "project_id": project_id,
                        "session_id": session_id,
                        "recorded_at": now,
                    },
                )

            await self._event_bus.publish(
                "cost_recorded",
                {"model": model, "cost_usd": cost, "project_id": project_id},
            )

            logger.debug(
                "Cost attributed: model=%s in=%d out=%d cost=$%.4f project=%s",
                model,
                in_tok,
                out_tok,
                cost,
                project_id,
            )

    def _compute_delta(
        self,
        current: dict[str, Any],
        previous: dict[str, Any],
    ) -> dict[str, dict[str, int]]:
        """Return per-model token deltas between current and previous snapshots."""
        delta: dict[str, dict[str, int]] = {}
        cur_models = self._extract_model_data(current)
        prev_models = self._extract_model_data(previous)

        for model, cur_counts in cur_models.items():
            prev_counts = prev_models.get(model, {})
            in_delta = max(
                0,
                cur_counts.get("input_tokens", 0) - prev_counts.get("input_tokens", 0),
            )
            out_delta = max(
                0,
                cur_counts.get("output_tokens", 0) - prev_counts.get("output_tokens", 0),
            )
            if in_delta > 0 or out_delta > 0:
                delta[model] = {"input_tokens": in_delta, "output_tokens": out_delta}

        return delta

    def _extract_model_data(self, data: dict[str, Any]) -> dict[str, dict[str, int]]:
        """Extract per-model token counts from stats cache (multiple formats)."""
        models: dict[str, dict[str, int]] = {}

        # Format 1: {"models": {"claude-X": {"input_tokens": N, "output_tokens": N}}}
        if "models" in data and isinstance(data["models"], dict):
            for model, counts in data["models"].items():
                if isinstance(counts, dict):
                    models[str(model)] = {
                        "input_tokens": int(counts.get("input_tokens", 0)),
                        "output_tokens": int(counts.get("output_tokens", 0)),
                    }
        # Format 2: flat with top-level model key
        elif "model" in data and "input_tokens" in data:
            model = str(data["model"])
            models[model] = {
                "input_tokens": int(data.get("input_tokens", 0)),
                "output_tokens": int(data.get("output_tokens", 0)),
            }
        # Format 3: list of session objects
        elif "sessions" in data and isinstance(data["sessions"], list):
            for sess in data["sessions"]:
                if isinstance(sess, dict) and "model" in sess:
                    model = str(sess["model"])
                    if model not in models:
                        models[model] = {"input_tokens": 0, "output_tokens": 0}
                    models[model]["input_tokens"] += int(sess.get("input_tokens", 0))
                    models[model]["output_tokens"] += int(sess.get("output_tokens", 0))

        return models

    async def record_explicit(
        self,
        session_id: str,
        input_tokens: int,
        output_tokens: int,
        model: str,
        cost_usd: float,
    ) -> None:
        """Record explicitly-reported cost from the atc-tower cost CLI.

        Marks the session as having explicit reporting so the stats-cache
        polling loop skips it and avoids double-counting.
        """
        self._has_explicit_reporting.add(session_id)

        project_id: str | None = None
        try:
            cursor = await self._db.execute(
                "SELECT project_id FROM sessions WHERE id = ?", (session_id,)
            )
            row = await cursor.fetchone()
            if row:
                project_id = str(row[0])
        except Exception:
            logger.debug(
                "Failed to find project for explicit cost: session=%s", session_id
            )

        now = datetime.now(UTC).isoformat()
        event_id = str(uuid.uuid4())

        await self._db.execute(
            """INSERT INTO usage_events
               (id, project_id, session_id, event_type, model,
                input_tokens, output_tokens, cost_usd, recorded_at)
               VALUES (?, ?, ?, 'ai_cost', ?, ?, ?, ?, ?)""",
            (event_id, project_id, session_id, model, input_tokens, output_tokens, cost_usd, now),
        )
        await self._db.commit()

        if self._ws_hub:
            await self._ws_hub.broadcast(
                "costs",
                {
                    "event_id": event_id,
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": cost_usd,
                    "project_id": project_id,
                    "session_id": session_id,
                    "recorded_at": now,
                    "source": "explicit",
                },
            )

        await self._event_bus.publish(
            "cost_recorded",
            {
                "model": model,
                "cost_usd": cost_usd,
                "project_id": project_id,
                "source": "explicit",
            },
        )

        logger.debug(
            "Explicit cost recorded: session=%s model=%s in=%d out=%d cost=$%.4f",
            session_id,
            model,
            input_tokens,
            output_tokens,
            cost_usd,
        )

    async def _on_cost_reported(self, data: dict[str, Any]) -> None:
        """Handle cost_reported event fired by the atc-tower cost CLI endpoint."""
        session_id = data.get("session_id")
        input_tokens = int(data.get("input_tokens", 0))
        output_tokens = int(data.get("output_tokens", 0))
        model = str(data.get("model", _FALLBACK_MODEL))
        cost_usd = float(
            data.get("cost_usd") or calculate_cost(model, input_tokens, output_tokens)
        )

        if not session_id:
            logger.warning("cost_reported event missing session_id — ignoring")
            return

        await self.record_explicit(session_id, input_tokens, output_tokens, model, cost_usd)

    async def _find_active_session(self) -> tuple[str | None, str | None]:
        """Return (session_id, project_id) for the most recently active session."""
        try:
            cursor = await self._db.execute(
                """SELECT id, project_id FROM sessions
                   WHERE status IN ('working', 'idle', 'connecting')
                   ORDER BY updated_at DESC LIMIT 1""",
            )
            row = await cursor.fetchone()
            if row:
                return str(row[0]), str(row[1])
        except Exception:
            logger.debug("Failed to find active session for cost attribution")
        return None, None

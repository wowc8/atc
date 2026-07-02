"""AI token tracker — polls ~/.claude/stats-cache.json for usage deltas.

Every ``poll_interval`` seconds the tracker reads the cumulative token counts
from Claude Code's stats cache, computes the delta against the previous
snapshot, attributes tokens to the most recently active session, and writes a
``usage_events`` row to the database.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from atc.state.db import write_usage_event

if TYPE_CHECKING:
    import aiosqlite

    from atc.api.ws.hub import WsHub
    from atc.core.events import EventBus

logger = logging.getLogger(__name__)

STATS_CACHE_PATH = Path.home() / ".claude" / "stats-cache.json"


@dataclass(frozen=True, slots=True)
class TokenUsageIncrement:
    """Provider-neutral, datetime-stamped token usage increment.

    Provider-specific collectors may parse cumulative usage however they need to,
    but they must cross the shared ATC boundary as positive increments. This
    class intentionally contains no Codex/Claude filesystem or event-shape
    knowledge.
    """

    session_id: str
    project_id: str | None
    provider: str
    model: str | None
    recorded_at: datetime
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0
    source: str = "provider"
    external_session_id: str | None = None
    source_event_id: str | None = None
    source_file: str | None = None
    source_offset: int | None = None
    raw_usage: dict[str, Any] | None = None

    def effective_total_tokens(self) -> int:
        """Return canonical total for aggregate displays/budgets."""
        if self.total_tokens > 0:
            return self.total_tokens
        return self.input_tokens + self.output_tokens + self.reasoning_output_tokens


class TokenUsageRecorder:
    """Provider-neutral persistence/fanout for token usage increments."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        event_bus: EventBus,
        *,
        ws_hub: WsHub | None = None,
    ) -> None:
        self._db = db
        self._event_bus = event_bus
        self._ws_hub = ws_hub

    async def record_increment(self, increment: TokenUsageIncrement) -> str | None:
        """Record a provider-neutral token usage increment.

        Returns the inserted event id, or ``None`` when the increment has no
        positive token usage. Negative token values are rejected to keep provider
        collectors from crossing the boundary with cumulative counter resets.
        """
        values = {
            "input_tokens": increment.input_tokens,
            "cached_input_tokens": increment.cached_input_tokens,
            "output_tokens": increment.output_tokens,
            "reasoning_output_tokens": increment.reasoning_output_tokens,
            "total_tokens": increment.total_tokens,
        }
        negative = {name: value for name, value in values.items() if value < 0}
        if negative:
            raise ValueError(f"Token usage increments cannot be negative: {negative}")

        effective_total = increment.effective_total_tokens()
        if effective_total == 0:
            logger.debug(
                "Ignoring zero token usage increment provider=%s source=%s session=%s",
                increment.provider,
                increment.source,
                increment.session_id,
            )
            return None

        event = await write_usage_event(
            self._db,
            "ai_tokens",
            project_id=increment.project_id,
            session_id=increment.session_id,
            model=increment.model,
            provider=increment.provider,
            source=increment.source,
            input_tokens=increment.input_tokens,
            cached_input_tokens=increment.cached_input_tokens,
            output_tokens=increment.output_tokens,
            reasoning_output_tokens=increment.reasoning_output_tokens,
            total_tokens=effective_total,
            external_session_id=increment.external_session_id,
            source_event_id=increment.source_event_id,
            source_file=increment.source_file,
            source_offset=increment.source_offset,
            raw_usage_json=(
                json.dumps(increment.raw_usage) if increment.raw_usage is not None else None
            ),
            recorded_at=increment.recorded_at.isoformat(),
        )

        payload = {
            "event_id": event.id,
            "model": increment.model,
            "provider": increment.provider,
            "source": increment.source,
            "input_tokens": increment.input_tokens,
            "cached_input_tokens": increment.cached_input_tokens,
            "output_tokens": increment.output_tokens,
            "reasoning_output_tokens": increment.reasoning_output_tokens,
            "total_tokens": effective_total,
            "project_id": increment.project_id,
            "session_id": increment.session_id,
            "external_session_id": increment.external_session_id,
            "recorded_at": event.recorded_at,
        }
        if self._ws_hub:
            await self._ws_hub.broadcast("tokens", payload)
        await self._event_bus.publish("tokens_recorded", payload)
        return event.id


class TokenTracker:
    """Polls ~/.claude/stats-cache.json and attributes token deltas to sessions."""

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
        self._recorder = TokenUsageRecorder(db, event_bus, ws_hub=ws_hub)
        self._task: asyncio.Task[None] | None = None
        # Sessions that report tokens explicitly via atc-tower tokens CLI.
        # Stats-cache polling is skipped for these to avoid double-counting.
        self._has_explicit_reporting: set[str] = set()

        # Subscribe to explicit token reports from the atc-tower tokens CLI
        self._event_bus.subscribe("tokens_reported", self._on_tokens_reported)

    async def start(self) -> None:
        """Start the background polling loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "TokenTracker started (interval=%.0fs, path=%s)",
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
            logger.info("TokenTracker stopped")

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._poll_once()
            except Exception:
                logger.exception("TokenTracker poll failed")
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

        # Skip attribution if this session reports tokens explicitly — avoid double-counting
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

            await self._recorder.record_increment(
                TokenUsageIncrement(
                    session_id=session_id or "unknown",
                    project_id=project_id,
                    provider="claude_code",
                    model=model,
                    recorded_at=datetime.now(UTC),
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    source="claude_stats_cache",
                )
            )

            logger.debug(
                "Tokens attributed: model=%s in=%d out=%d project=%s",
                model,
                in_tok,
                out_tok,
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
    ) -> None:
        """Record explicitly-reported token usage from the atc-tower tokens CLI.

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
                "Failed to find project for explicit token report: session=%s", session_id
            )

        await self._recorder.record_increment(
            TokenUsageIncrement(
                session_id=session_id,
                project_id=project_id,
                provider="explicit",
                model=model,
                recorded_at=datetime.now(UTC),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                source="explicit",
            )
        )

        logger.debug(
            "Explicit tokens recorded: session=%s model=%s in=%d out=%d",
            session_id,
            model,
            input_tokens,
            output_tokens,
        )

    async def _on_tokens_reported(self, data: dict[str, Any]) -> None:
        """Handle tokens_reported event fired by the atc-tower tokens CLI endpoint."""
        session_id = data.get("session_id")
        input_tokens = int(data.get("input_tokens", 0))
        output_tokens = int(data.get("output_tokens", 0))
        model = str(data.get("model", "unknown"))
        if not session_id:
            logger.warning("tokens_reported event missing session_id — ignoring")
            return

        await self.record_explicit(session_id, input_tokens, output_tokens, model)

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
            logger.debug("Failed to find active session for token attribution")
        return None, None

"""Codex provider token usage collection.

This module is the Codex-owned boundary for Codex JSONL discovery, parsing,
session mapping, and cumulative-counter delta conversion. Shared token tracking
only receives provider-neutral ``TokenUsageIncrement`` values from here.
"""

from __future__ import annotations

import asyncio
import contextlib
import glob
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from atc.state.db import get_session, get_usage_source_offset, upsert_usage_source_offset
from atc.tracking.tokens import TokenUsageIncrement, TokenUsageRecorder

if TYPE_CHECKING:
    import aiosqlite

    from atc.api.ws.hub import WsHub
    from atc.core.events import EventBus
    from atc.state.models import Session, UsageSourceOffset

logger = logging.getLogger(__name__)

CODEX_PROVIDER = "codex"
CODEX_SOURCE = "codex_jsonl"
DEFAULT_CODEX_SESSIONS_GLOB = "~/.codex/sessions/**/*.jsonl"
_TOKEN_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


@dataclass(frozen=True, slots=True)
class CodexTokenSnapshot:
    """Cumulative token snapshot parsed from a Codex session JSONL file."""

    external_session_id: str | None
    source_file: Path
    source_offset: int
    recorded_at: datetime
    model: str | None
    cwd: Path | None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0
    raw: dict[str, Any] | None = None

    def source_key(self) -> str:
        """Return Codex-owned durable source key for high-water state."""
        return str(self.source_file)


@dataclass(frozen=True, slots=True)
class CodexUsageSyncStatus:
    """Operator-visible status for the Codex usage sync service."""

    enabled: bool
    running: bool
    sessions_glob: str
    poll_interval_seconds: float
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_inserted_events: int = 0
    last_discovered_files: int = 0
    last_error: str | None = None


class CodexJsonlParser:
    """Parse Codex JSONL and yield token-count snapshots.

    The parser keeps Codex event-shape knowledge here, not in the shared token
    recorder. Malformed lines and non-token events are skipped.
    """

    def parse_file(self, path: Path, *, start_offset: int = 0) -> list[CodexTokenSnapshot]:
        snapshots: list[CodexTokenSnapshot] = []
        external_session_id: str | None = None
        model: str | None = None
        cwd: Path | None = None
        offset = 0

        try:
            with path.open("rb") as handle:
                if start_offset > 0:
                    handle.seek(start_offset)
                    offset = start_offset
                for raw_line in handle:
                    offset += len(raw_line)
                    line = raw_line.decode("utf-8", errors="replace")
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        logger.debug("Skipping malformed Codex JSONL line path=%s", path)
                        continue

                    external_session_id = self._external_session_id(
                        event, fallback=external_session_id
                    )
                    model = self._model(event, fallback=model)
                    cwd = self._cwd(event, fallback=cwd)

                    snapshot = self._token_snapshot(
                        event,
                        path=path,
                        source_offset=offset,
                        external_session_id=external_session_id,
                        model=model,
                        cwd=cwd,
                    )
                    if snapshot is not None:
                        snapshots.append(snapshot)
        except FileNotFoundError:
            logger.debug("Codex JSONL disappeared before parsing: %s", path)
        except OSError as exc:
            logger.warning("Failed to parse Codex JSONL %s: %s", path, exc)
        return snapshots

    def _token_snapshot(
        self,
        event: dict[str, Any],
        *,
        path: Path,
        source_offset: int,
        external_session_id: str | None,
        model: str | None,
        cwd: Path | None,
    ) -> CodexTokenSnapshot | None:
        if event.get("type") != "event_msg":
            return None
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "token_count":
            return None
        info = payload.get("info")
        if not isinstance(info, dict):
            return None
        usage = info.get("total_token_usage")
        if not isinstance(usage, dict):
            return None

        recorded_at = self._recorded_at(event)
        return CodexTokenSnapshot(
            external_session_id=external_session_id,
            source_file=path,
            source_offset=source_offset,
            recorded_at=recorded_at,
            model=model,
            cwd=cwd,
            input_tokens=self._int_token(usage, "input_tokens"),
            cached_input_tokens=self._int_token(usage, "cached_input_tokens"),
            output_tokens=self._int_token(usage, "output_tokens"),
            reasoning_output_tokens=self._int_token(usage, "reasoning_output_tokens"),
            total_tokens=self._int_token(usage, "total_tokens"),
            raw=payload,
        )

    def _external_session_id(self, event: dict[str, Any], *, fallback: str | None) -> str | None:
        if event.get("type") != "session_meta":
            return fallback
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return fallback
        raw_id = payload.get("id")
        return str(raw_id) if raw_id else fallback

    def _model(self, event: dict[str, Any], *, fallback: str | None) -> str | None:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return fallback
        raw_model = payload.get("model")
        return str(raw_model) if raw_model else fallback

    def _cwd(self, event: dict[str, Any], *, fallback: Path | None) -> Path | None:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return fallback
        raw_cwd = payload.get("cwd")
        if not raw_cwd:
            return fallback
        return Path(str(raw_cwd))

    def _recorded_at(self, event: dict[str, Any]) -> datetime:
        raw_timestamp = event.get("timestamp")
        if isinstance(raw_timestamp, str):
            with contextlib.suppress(ValueError):
                return datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
        return datetime.now(UTC)

    def _int_token(self, usage: dict[str, Any], key: str) -> int:
        value = usage.get(key, 0)
        if value is None:
            return 0
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0


class CodexUsageSyncService:
    """Poll Codex JSONL files and emit provider-neutral token increments."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        event_bus: EventBus,
        *,
        ws_hub: WsHub | None = None,
        sessions_glob: str = DEFAULT_CODEX_SESSIONS_GLOB,
        poll_interval: float = 5.0,
        parser: CodexJsonlParser | None = None,
        recorder: TokenUsageRecorder | None = None,
    ) -> None:
        self._db = db
        self._sessions_glob = sessions_glob
        self._poll_interval = poll_interval
        self._parser = parser or CodexJsonlParser()
        self._recorder = recorder or TokenUsageRecorder(db, event_bus, ws_hub=ws_hub)
        self._task: asyncio.Task[None] | None = None
        self._last_started_at: datetime | None = None
        self._last_finished_at: datetime | None = None
        self._last_inserted_events = 0
        self._last_discovered_files = 0
        self._last_error: str | None = None

    async def start(self) -> None:
        """Start the Codex usage polling loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "CodexUsageSyncService started (interval=%.0fs, glob=%s)",
            self._poll_interval,
            self._sessions_glob,
        )

    async def stop(self) -> None:
        """Stop the Codex usage polling loop."""
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("CodexUsageSyncService stopped")

    async def sync_once(self) -> int:
        """Synchronize currently discoverable Codex JSONL files once.

        Returns the number of usage-event rows inserted.
        """
        self._last_started_at = datetime.now(UTC)
        self._last_error = None
        inserted = 0
        paths = self._discover_files()
        self._last_discovered_files = len(paths)
        try:
            for path in paths:
                inserted += await self._sync_file(path)
        except Exception as exc:
            self._last_error = str(exc)
            raise
        finally:
            self._last_inserted_events = inserted
            self._last_finished_at = datetime.now(UTC)
        return inserted

    def status(self, *, enabled: bool = True) -> CodexUsageSyncStatus:
        """Return current service status without triggering a sync."""
        return CodexUsageSyncStatus(
            enabled=enabled,
            running=self._task is not None and not self._task.done(),
            sessions_glob=self._sessions_glob,
            poll_interval_seconds=self._poll_interval,
            last_started_at=self._last_started_at,
            last_finished_at=self._last_finished_at,
            last_inserted_events=self._last_inserted_events,
            last_discovered_files=self._last_discovered_files,
            last_error=self._last_error,
        )

    def _discover_files(self) -> list[Path]:
        expanded = str(Path(self._sessions_glob).expanduser())
        paths = [Path(p) for p in glob.glob(expanded, recursive=True)]
        return sorted((p for p in paths if p.is_file()), key=lambda p: p.stat().st_mtime)

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.sync_once()
            except Exception:
                logger.exception("Codex usage sync failed")
            await asyncio.sleep(self._poll_interval)

    async def _sync_file(self, path: Path) -> int:
        offset = await get_usage_source_offset(
            self._db,
            provider=CODEX_PROVIDER,
            source_key=str(path),
        )
        start_offset = offset.byte_offset if offset is not None else 0
        snapshots = self._parser.parse_file(path, start_offset=start_offset)
        inserted = 0
        for snapshot in snapshots:
            row_id = await self._record_snapshot_delta(snapshot, offset)
            if row_id is not None:
                inserted += 1
            offset = await get_usage_source_offset(
                self._db,
                provider=CODEX_PROVIDER,
                source_key=snapshot.source_key(),
            )
        return inserted

    async def _record_snapshot_delta(
        self,
        snapshot: CodexTokenSnapshot,
        previous: UsageSourceOffset | None,
    ) -> str | None:
        session = await self._map_snapshot_to_session(snapshot)
        if session is None:
            logger.info(
                "Skipping unmapped Codex token snapshot file=%s offset=%s "
                "external_session=%s cwd=%s",
                snapshot.source_file,
                snapshot.source_offset,
                snapshot.external_session_id,
                snapshot.cwd,
            )
            await self._store_high_water(snapshot)
            return None

        deltas = self._compute_delta(snapshot, previous)
        await self._store_high_water(snapshot)
        if all(value == 0 for value in deltas.values()):
            return None

        increment = TokenUsageIncrement(
            session_id=session.id,
            project_id=session.project_id,
            provider=CODEX_PROVIDER,
            model=snapshot.model,
            recorded_at=snapshot.recorded_at,
            input_tokens=deltas["input_tokens"],
            cached_input_tokens=deltas["cached_input_tokens"],
            output_tokens=deltas["output_tokens"],
            reasoning_output_tokens=deltas["reasoning_output_tokens"],
            total_tokens=deltas["total_tokens"],
            source=CODEX_SOURCE,
            external_session_id=snapshot.external_session_id,
            source_event_id=f"{snapshot.source_file}:{snapshot.source_offset}",
            source_file=str(snapshot.source_file),
            source_offset=snapshot.source_offset,
            raw_usage=snapshot.raw,
        )
        return await self._recorder.record_increment(increment)

    def _compute_delta(
        self,
        snapshot: CodexTokenSnapshot,
        previous: UsageSourceOffset | None,
    ) -> dict[str, int]:
        if previous is None:
            return {key: getattr(snapshot, key) for key in _TOKEN_KEYS}

        raw_deltas = {
            "input_tokens": snapshot.input_tokens - previous.last_input_tokens,
            "cached_input_tokens": (
                snapshot.cached_input_tokens - previous.last_cached_input_tokens
            ),
            "output_tokens": snapshot.output_tokens - previous.last_output_tokens,
            "reasoning_output_tokens": (
                snapshot.reasoning_output_tokens - previous.last_reasoning_output_tokens
            ),
            "total_tokens": snapshot.total_tokens - previous.last_total_tokens,
        }
        if any(value < 0 for value in raw_deltas.values()):
            logger.warning(
                "Codex token counter moved backwards file=%s offset=%s; not emitting delta",
                snapshot.source_file,
                snapshot.source_offset,
            )
            return dict.fromkeys(_TOKEN_KEYS, 0)
        return raw_deltas

    async def _store_high_water(self, snapshot: CodexTokenSnapshot) -> None:
        await upsert_usage_source_offset(
            self._db,
            provider=CODEX_PROVIDER,
            source_key=snapshot.source_key(),
            external_session_id=snapshot.external_session_id,
            byte_offset=snapshot.source_offset,
            last_input_tokens=snapshot.input_tokens,
            last_cached_input_tokens=snapshot.cached_input_tokens,
            last_output_tokens=snapshot.output_tokens,
            last_reasoning_output_tokens=snapshot.reasoning_output_tokens,
            last_total_tokens=snapshot.total_tokens,
        )

    async def _map_snapshot_to_session(self, snapshot: CodexTokenSnapshot) -> Session | None:
        """Map Codex metadata to an ATC session without creating orphan rows."""
        candidate_ids = self._candidate_session_ids(snapshot)
        for session_id in candidate_ids:
            session = await get_session(self._db, session_id)
            if session is not None and session.provider == CODEX_PROVIDER:
                return session

        previous_session_id = await self._previous_usage_session_id(snapshot)
        if previous_session_id is not None:
            session = await get_session(self._db, previous_session_id)
            if session is not None and session.provider == CODEX_PROVIDER:
                return session
        return None

    async def _previous_usage_session_id(self, snapshot: CodexTokenSnapshot) -> str | None:
        """Reuse source-file mapping after appended lines omit session metadata."""
        cursor = await self._db.execute(
            """SELECT session_id FROM usage_events
               WHERE provider = ? AND source = ? AND source_file = ?
               ORDER BY COALESCE(source_offset, 0) DESC LIMIT 1""",
            (CODEX_PROVIDER, CODEX_SOURCE, str(snapshot.source_file)),
        )
        row = await cursor.fetchone()
        if row is None or row["session_id"] is None:
            return None
        return str(row["session_id"])

    def _candidate_session_ids(self, snapshot: CodexTokenSnapshot) -> list[str]:
        candidates: list[str] = []
        if snapshot.cwd is not None:
            candidates.append(snapshot.cwd.name)
            parts = [part for part in snapshot.cwd.parts if part]
            candidates.extend(parts)
        if snapshot.external_session_id:
            candidates.append(snapshot.external_session_id)

        deduped: list[str] = []
        for candidate in candidates:
            if candidate and candidate not in deduped:
                deduped.append(candidate)
        return deduped

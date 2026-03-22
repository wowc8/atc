"""LTM consolidation — synthesises recent session events into long-term learnings.

The consolidation job:
1. Reads all current ace_stm entries + recent project session_log entries (last 3h).
2. Reads recently-resolved failure_log entries (learnings from failures).
3. Calls Claude claude-sonnet-4-6 with a synthesis prompt.
4. Writes each synthesised learning to tower_memory via LongTermMemory.write().
5. Records the run in memory_consolidation_runs.
6. Prunes ace_stm entries older than 24h.

Usage::

    result = await MemoryConsolidation.run_consolidation(db, event_bus, ws_hub)
    ok     = await MemoryConsolidation.should_run(db)
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from atc.memory.ace_stm import AceSTM
from atc.memory.ltm import LongTermMemory

if TYPE_CHECKING:
    import aiosqlite

    from atc.api.ws.hub import WsHub
    from atc.core.events import EventBus

logger = logging.getLogger(__name__)

_SYNTHESIS_PROMPT = """\
You are analysing recent ATC (AI orchestration platform) session events to extract \
long-term learnings for the system memory.

Here are recent events (ace session snapshots, project decisions/findings, \
and resolved failure log entries):

{events_json}

Extract 2-5 cross-cutting patterns or learnings worth remembering long-term.
Focus on:
- Recurring failure patterns and how they were resolved
- Effective task decomposition strategies that worked well
- Integration or environment quirks that caused issues
- Workflow improvements that increased velocity

Respond with ONLY a valid JSON array of objects. Each object must have:
  "key"        — short slug (kebab-case, ≤ 50 chars)
  "value"      — 1-3 sentence description of the learning
  "project_id" — project UUID if project-specific, or null for system-wide

Example:
[
  {{"key": "tmux-pane-init-delay", "value": "Tmux panes need 500 ms after spawn before accepting input.", "project_id": null}}
]
"""


@dataclass
class ConsolidationResult:
    """Result of a single consolidation run."""

    run_id: str
    entries_processed: int
    entries_written: int
    status: str  # done | skipped | error
    error_message: str | None = None


class MemoryConsolidation:
    """LTM consolidation logic."""

    @staticmethod
    async def should_run(db: aiosqlite.Connection) -> bool:
        """Return True if a consolidation run is due.

        Runs if:
        - No previous run recorded, OR
        - Last finished run was >= 3 hours ago.
        """
        cursor = await db.execute(
            """SELECT finished_at FROM memory_consolidation_runs
               WHERE status = 'done'
               ORDER BY finished_at DESC LIMIT 1"""
        )
        row = await cursor.fetchone()
        if row is None or row["finished_at"] is None:
            return True
        try:
            last_run = datetime.fromisoformat(str(row["finished_at"]))
            return (datetime.now(UTC) - last_run).total_seconds() >= 3 * 3600
        except ValueError:
            return True

    @staticmethod
    async def should_run_for_day(db: aiosqlite.Connection) -> bool:
        """Return True if no consolidation has run since midnight UTC today."""
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        cursor = await db.execute(
            """SELECT COUNT(*) AS cnt FROM memory_consolidation_runs
               WHERE status = 'done' AND finished_at >= ?""",
            (midnight.isoformat(),),
        )
        row = await cursor.fetchone()
        return (row["cnt"] if row else 0) == 0

    # ------------------------------------------------------------------
    # Core consolidation run
    # ------------------------------------------------------------------

    @staticmethod
    async def run_consolidation(
        db: aiosqlite.Connection,
        event_bus: EventBus,
        ws_hub: WsHub | None = None,
    ) -> ConsolidationResult:
        """Execute one full consolidation cycle.

        Reads recent events, synthesises learnings via Claude, writes to LTM,
        prunes old STM, and records the run.

        Returns a :class:`ConsolidationResult`.
        """
        run_id = str(uuid.uuid4())
        started_at = datetime.now(UTC).isoformat()

        # Record the run as 'running'
        await db.execute(
            """INSERT INTO memory_consolidation_runs
               (id, started_at, entries_processed, entries_written, status)
               VALUES (?, ?, 0, 0, 'running')""",
            (run_id, started_at),
        )
        await db.commit()

        try:
            events = await _collect_recent_events(db)
            entries_processed = len(events)

            if not events:
                logger.info("Consolidation %s: no events to process, skipping synthesis", run_id)
                await _finish_run(db, run_id, "done", 0, 0)
                return ConsolidationResult(
                    run_id=run_id,
                    entries_processed=0,
                    entries_written=0,
                    status="done",
                )

            learnings = await _synthesise_learnings(events)
            entries_written = 0

            for item in learnings:
                key = item.get("key", "")
                value = item.get("value", "")
                project_id = item.get("project_id")
                if not key or not value:
                    continue
                try:
                    await LongTermMemory.write(db, key, str(value), project_id=project_id)
                    entries_written += 1
                except Exception:
                    logger.exception("Failed to write LTM entry key=%r", key)

            # Prune stale STM entries
            await AceSTM.prune_old(db, max_age_hours=24)

            await _finish_run(db, run_id, "done", entries_processed, entries_written)
            logger.info(
                "Consolidation %s complete: processed=%d written=%d",
                run_id,
                entries_processed,
                entries_written,
            )

            # Broadcast completion to dashboard
            if ws_hub is not None:
                await ws_hub.broadcast(
                    "memory",
                    {
                        "consolidation_complete": True,
                        "run_id": run_id,
                        "entries_written": entries_written,
                    },
                )

            await event_bus.publish(
                "memory_consolidation_complete",
                {"run_id": run_id, "entries_written": entries_written},
            )

            return ConsolidationResult(
                run_id=run_id,
                entries_processed=entries_processed,
                entries_written=entries_written,
                status="done",
            )

        except Exception as exc:
            logger.exception("Consolidation %s failed", run_id)
            await _finish_run(db, run_id, "error", 0, 0)
            return ConsolidationResult(
                run_id=run_id,
                entries_processed=0,
                entries_written=0,
                status="error",
                error_message=str(exc),
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _collect_recent_events(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    """Gather STM snapshots + recent project log + resolved failure logs."""
    events: list[dict[str, Any]] = []
    cutoff = (datetime.now(UTC) - timedelta(hours=3)).isoformat()

    # Ace STM snapshots
    stm_entries = await AceSTM.list_all(db)
    for e in stm_entries:
        events.append({"source": "ace_stm", **e})

    # Recent session_log context entries (project decisions/findings from last 3h)
    cursor = await db.execute(
        """SELECT project_id, value FROM context_entries
           WHERE key = 'session_log' AND scope = 'project'
             AND updated_at >= ?""",
        (cutoff,),
    )
    rows = await cursor.fetchall()
    for row in rows:
        try:
            entries: list[Any] = json.loads(row["value"])
            if isinstance(entries, list):
                for entry in entries[-10:]:  # last 10 per project
                    events.append({"source": "project_log", "project_id": row["project_id"], **entry})
        except (json.JSONDecodeError, TypeError):
            pass

    # Recently resolved failure logs (learnings from failures)
    cursor = await db.execute(
        """SELECT level, category, message, project_id, entity_type
           FROM failure_logs
           WHERE resolved = 1 AND created_at >= ?
           ORDER BY created_at DESC LIMIT 20""",
        (cutoff,),
    )
    rows = await cursor.fetchall()
    for row in rows:
        events.append({"source": "failure_log_resolved", **dict(row)})

    return events


async def _synthesise_learnings(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Call Claude to extract cross-cutting learnings from events.

    Returns a list of {key, value, project_id} dicts.  Returns [] on any failure.
    """
    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("anthropic package not installed — skipping synthesis")
        return []

    events_json = json.dumps(events, indent=2, default=str)
    prompt = _SYNTHESIS_PROMPT.format(events_json=events_json)

    try:
        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text if response.content else ""
        learnings: list[dict[str, Any]] = json.loads(raw)
        if not isinstance(learnings, list):
            logger.warning("Synthesis returned non-list: %r", type(learnings))
            return []
        return learnings
    except anthropic.APIConnectionError:
        logger.warning("Anthropic API not reachable — skipping synthesis")
        return []
    except (json.JSONDecodeError, IndexError, KeyError):
        logger.exception("Failed to parse synthesis response")
        return []
    except Exception:
        logger.exception("Unexpected error during synthesis")
        return []


async def _finish_run(
    db: aiosqlite.Connection,
    run_id: str,
    status: str,
    entries_processed: int,
    entries_written: int,
) -> None:
    """Update the consolidation run row with final status."""
    finished_at = datetime.now(UTC).isoformat()
    await db.execute(
        """UPDATE memory_consolidation_runs
           SET finished_at = ?, status = ?, entries_processed = ?, entries_written = ?
           WHERE id = ?""",
        (finished_at, status, entries_processed, entries_written, run_id),
    )
    await db.commit()

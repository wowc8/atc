"""Unit tests for Codex provider token usage collection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from atc.agents.codex_usage import (
    CODEX_PROVIDER,
    CODEX_SOURCE,
    CodexJsonlParser,
    CodexUsageSyncService,
)
from atc.core.events import EventBus
from atc.state.db import create_project, create_session, get_connection, run_migrations


@pytest.fixture
async def migrated_db(tmp_path: Path):
    db_path = tmp_path / "atc.db"
    await run_migrations(str(db_path))
    async with get_connection(str(db_path)) as conn:
        yield conn


def write_jsonl(path: Path, *events: dict[str, Any] | str) -> None:
    lines: list[str] = []
    for event in events:
        if isinstance(event, str):
            lines.append(event)
        else:
            lines.append(json.dumps(event))
    path.write_text("\n".join(lines) + "\n")


def session_meta(
    *, external_id: str = "codex-external", cwd: str = "/tmp/atc-session"
) -> dict[str, Any]:
    return {
        "timestamp": "2026-07-02T12:00:00.000Z",
        "type": "session_meta",
        "payload": {"id": external_id, "cwd": cwd},
    }


def turn_context(*, model: str = "gpt-5.5", cwd: str = "/tmp/atc-session") -> dict[str, Any]:
    return {
        "timestamp": "2026-07-02T12:00:01.000Z",
        "type": "turn_context",
        "payload": {"model": model, "cwd": cwd},
    }


def token_count(
    *,
    input_tokens: int = 100,
    cached_input_tokens: int = 40,
    output_tokens: int = 20,
    reasoning_output_tokens: int = 5,
    total_tokens: int = 125,
    timestamp: str = "2026-07-02T12:00:02.000Z",
) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_input_tokens,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": reasoning_output_tokens,
                    "total_tokens": total_tokens,
                },
                "last_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_input_tokens,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": reasoning_output_tokens,
                    "total_tokens": total_tokens,
                },
            },
        },
    }


class TestCodexJsonlParser:
    def test_parser_extracts_complete_token_count(self, tmp_path: Path) -> None:
        path = tmp_path / "rollout-session.jsonl"
        write_jsonl(path, session_meta(), turn_context(), token_count())

        snapshots = CodexJsonlParser().parse_file(path)

        assert len(snapshots) == 1
        snapshot = snapshots[0]
        assert snapshot.external_session_id == "codex-external"
        assert snapshot.model == "gpt-5.5"
        assert snapshot.cwd == Path("/tmp/atc-session")
        assert snapshot.input_tokens == 100
        assert snapshot.cached_input_tokens == 40
        assert snapshot.output_tokens == 20
        assert snapshot.reasoning_output_tokens == 5
        assert snapshot.total_tokens == 125
        assert snapshot.source_offset == path.stat().st_size
        assert snapshot.raw is not None
        assert snapshot.raw["type"] == "token_count"

    def test_parser_ignores_malformed_json_and_non_token_events(self, tmp_path: Path) -> None:
        path = tmp_path / "rollout-session.jsonl"
        write_jsonl(
            path,
            "{malformed",
            {
                "timestamp": "2026-07-02T12:00:00Z",
                "type": "event_msg",
                "payload": {"type": "task_started"},
            },
            token_count(),
        )

        snapshots = CodexJsonlParser().parse_file(path)

        assert len(snapshots) == 1
        assert snapshots[0].total_tokens == 125

    def test_parser_handles_missing_optional_token_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "rollout-session.jsonl"
        event = token_count()
        usage = event["payload"]["info"]["total_token_usage"]
        del usage["cached_input_tokens"]
        del usage["reasoning_output_tokens"]
        write_jsonl(path, event)

        snapshot = CodexJsonlParser().parse_file(path)[0]

        assert snapshot.input_tokens == 100
        assert snapshot.cached_input_tokens == 0
        assert snapshot.reasoning_output_tokens == 0
        assert snapshot.total_tokens == 125

    def test_parser_respects_start_offset_for_appended_jsonl(self, tmp_path: Path) -> None:
        path = tmp_path / "rollout-session.jsonl"
        write_jsonl(path, token_count(total_tokens=10, input_tokens=8, output_tokens=2))
        start_offset = path.stat().st_size
        with path.open("a") as handle:
            handle.write(
                json.dumps(token_count(total_tokens=25, input_tokens=20, output_tokens=5)) + "\n"
            )

        snapshots = CodexJsonlParser().parse_file(path, start_offset=start_offset)

        assert len(snapshots) == 1
        assert snapshots[0].total_tokens == 25
        assert snapshots[0].source_offset == path.stat().st_size


@pytest.mark.asyncio
class TestCodexUsageSyncService:
    async def test_first_cumulative_snapshot_records_increment(
        self, migrated_db, tmp_path: Path
    ) -> None:
        project = await create_project(migrated_db, "codex project", agent_provider=CODEX_PROVIDER)
        session = await create_session(
            migrated_db,
            project.id,
            "ace",
            "ace",
            provider=CODEX_PROVIDER,
            status="working",
        )
        path = tmp_path / "rollout-session.jsonl"
        cwd = f"/private/tmp/atc-agents/{session.id}"
        write_jsonl(path, session_meta(cwd=cwd), turn_context(cwd=cwd), token_count())

        service = CodexUsageSyncService(
            migrated_db,
            EventBus(),
            sessions_glob=str(tmp_path / "*.jsonl"),
        )
        inserted = await service.sync_once()

        assert inserted == 1
        cursor = await migrated_db.execute("SELECT * FROM usage_events")
        row = await cursor.fetchone()
        assert row["provider"] == CODEX_PROVIDER
        assert row["source"] == CODEX_SOURCE
        assert row["session_id"] == session.id
        assert row["project_id"] == project.id
        assert row["input_tokens"] == 100
        assert row["cached_input_tokens"] == 40
        assert row["output_tokens"] == 20
        assert row["reasoning_output_tokens"] == 5
        assert row["total_tokens"] == 125
        assert row["external_session_id"] == "codex-external"
        assert row["source_file"] == str(path)

    async def test_subsequent_cumulative_snapshot_records_only_delta(
        self, migrated_db, tmp_path: Path
    ) -> None:
        project = await create_project(migrated_db, "codex project", agent_provider=CODEX_PROVIDER)
        session = await create_session(
            migrated_db, project.id, "ace", "ace", provider=CODEX_PROVIDER
        )
        path = tmp_path / "rollout-session.jsonl"
        cwd = f"/private/tmp/atc-agents/{session.id}"
        write_jsonl(path, session_meta(cwd=cwd), turn_context(cwd=cwd), token_count())

        service = CodexUsageSyncService(migrated_db, EventBus(), sessions_glob=str(path))
        assert await service.sync_once() == 1
        with path.open("a") as handle:
            handle.write(
                json.dumps(
                    token_count(
                        input_tokens=130,
                        cached_input_tokens=55,
                        output_tokens=35,
                        reasoning_output_tokens=8,
                        total_tokens=173,
                        timestamp="2026-07-02T12:01:00.000Z",
                    )
                )
                + "\n"
            )

        assert await service.sync_once() == 1
        cursor = await migrated_db.execute(
            "SELECT input_tokens, cached_input_tokens, output_tokens, "
            "reasoning_output_tokens, total_tokens "
            "FROM usage_events ORDER BY recorded_at"
        )
        rows = await cursor.fetchall()
        assert rows[1]["input_tokens"] == 30
        assert rows[1]["cached_input_tokens"] == 15
        assert rows[1]["output_tokens"] == 15
        assert rows[1]["reasoning_output_tokens"] == 3
        assert rows[1]["total_tokens"] == 48

    async def test_reread_does_not_double_count(self, migrated_db, tmp_path: Path) -> None:
        project = await create_project(migrated_db, "codex project", agent_provider=CODEX_PROVIDER)
        session = await create_session(
            migrated_db, project.id, "ace", "ace", provider=CODEX_PROVIDER
        )
        path = tmp_path / "rollout-session.jsonl"
        cwd = f"/tmp/{session.id}"
        write_jsonl(path, session_meta(cwd=cwd), turn_context(cwd=cwd), token_count())
        service = CodexUsageSyncService(migrated_db, EventBus(), sessions_glob=str(path))

        assert await service.sync_once() == 1
        assert await service.sync_once() == 0

        cursor = await migrated_db.execute("SELECT COUNT(*) FROM usage_events")
        row = await cursor.fetchone()
        assert row[0] == 1

    async def test_unmapped_session_skips_without_orphan_usage_row(
        self, migrated_db, tmp_path: Path
    ) -> None:
        path = tmp_path / "rollout-session.jsonl"
        write_jsonl(path, session_meta(cwd="/tmp/not-an-atc-session"), token_count())
        service = CodexUsageSyncService(migrated_db, EventBus(), sessions_glob=str(path))

        assert await service.sync_once() == 0
        cursor = await migrated_db.execute("SELECT COUNT(*) FROM usage_events")
        row = await cursor.fetchone()
        assert row[0] == 0

    async def test_rolled_back_counter_does_not_emit_negative_increment(
        self, migrated_db, tmp_path: Path
    ) -> None:
        project = await create_project(migrated_db, "codex project", agent_provider=CODEX_PROVIDER)
        session = await create_session(
            migrated_db, project.id, "ace", "ace", provider=CODEX_PROVIDER
        )
        path = tmp_path / "rollout-session.jsonl"
        cwd = f"/tmp/{session.id}"
        write_jsonl(
            path,
            session_meta(cwd=cwd),
            token_count(total_tokens=100, input_tokens=80, output_tokens=20),
        )
        service = CodexUsageSyncService(migrated_db, EventBus(), sessions_glob=str(path))
        assert await service.sync_once() == 1
        with path.open("a") as handle:
            handle.write(
                json.dumps(token_count(total_tokens=90, input_tokens=70, output_tokens=20)) + "\n"
            )

        assert await service.sync_once() == 0
        cursor = await migrated_db.execute("SELECT COUNT(*) FROM usage_events")
        row = await cursor.fetchone()
        assert row[0] == 1


def test_codex_parsing_stays_inside_codex_module() -> None:
    shared = Path("src/atc/tracking/tokens.py").read_text()
    forbidden = [".codex", "token_count", "rollout-", "codex_jsonl"]
    assert [term for term in forbidden if term in shared] == []

    codex_module = Path("src/atc/agents/codex_usage.py").read_text()
    assert "token_count" in codex_module
    assert "~/.codex/sessions/**/*.jsonl" in codex_module

"""Runtime integration tests for Codex token usage sync."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient

from atc.api.app import create_app
from atc.config import Settings
from atc.providers.codex.usage import CodexUsageSyncStatus
from atc.state import db as db_ops

if TYPE_CHECKING:
    from pathlib import Path


class FakeCodexUsageSyncService:
    instances: list[FakeCodexUsageSyncService] = []

    def __init__(
        self,
        *args: Any,
        sessions_glob: str,
        poll_interval: float,
        **kwargs: Any,
    ) -> None:
        self.sessions_glob = sessions_glob
        self.poll_interval = poll_interval
        self.started = False
        self.stopped = False
        self.sync_count = 0
        FakeCodexUsageSyncService.instances.append(self)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def sync_once(self) -> int:
        self.sync_count += 1
        return 3

    def status(self, *, enabled: bool = True) -> CodexUsageSyncStatus:
        return CodexUsageSyncStatus(
            enabled=enabled,
            running=self.started and not self.stopped,
            sessions_glob=self.sessions_glob,
            poll_interval_seconds=self.poll_interval,
            last_inserted_events=3 if self.sync_count else 0,
            last_discovered_files=2,
        )


def settings_for(
    tmp_path: Path,
    *,
    codex_enabled: bool = True,
    glob_value: str = "*.jsonl",
) -> Settings:
    return Settings(
        database={"path": str(tmp_path / "test.db")},
        token_tracker={
            "poll_interval_seconds": 123,
            "codex_enabled": codex_enabled,
            "codex_sessions_glob": glob_value,
        },
        backup={"auto_backup_enabled": False},
    )


def test_codex_usage_sync_starts_and_stops_with_app_lifecycle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from atc.providers.codex import usage as codex_usage

    FakeCodexUsageSyncService.instances = []
    monkeypatch.setattr(codex_usage, "CodexUsageSyncService", FakeCodexUsageSyncService)

    app = create_app(settings_for(tmp_path, glob_value=str(tmp_path / "*.jsonl")))
    with TestClient(app):
        service = FakeCodexUsageSyncService.instances[0]
        assert service.started is True
        assert service.sessions_glob == str(tmp_path / "*.jsonl")
        assert service.poll_interval == 123
    assert service.stopped is True


def test_codex_usage_sync_does_not_start_when_disabled(tmp_path: Path, monkeypatch) -> None:
    from atc.providers.codex import usage as codex_usage

    FakeCodexUsageSyncService.instances = []
    monkeypatch.setattr(codex_usage, "CodexUsageSyncService", FakeCodexUsageSyncService)

    app = create_app(settings_for(tmp_path, codex_enabled=False))
    with TestClient(app):
        service = FakeCodexUsageSyncService.instances[0]
        assert service.started is False
    assert service.stopped is True


def test_sync_codex_api_runs_one_service_pass(tmp_path: Path, monkeypatch) -> None:
    from atc.providers.codex import usage as codex_usage

    FakeCodexUsageSyncService.instances = []
    monkeypatch.setattr(codex_usage, "CodexUsageSyncService", FakeCodexUsageSyncService)

    app = create_app(settings_for(tmp_path))
    with TestClient(app) as client:
        response = client.post("/api/usage/tokens/sync-codex")
        assert response.status_code == 200
        assert response.json() == {"inserted_events": 3, "enabled": True}
        assert FakeCodexUsageSyncService.instances[0].sync_count == 1


def test_sync_codex_status_api_reports_service_state(tmp_path: Path, monkeypatch) -> None:
    from atc.providers.codex import usage as codex_usage

    FakeCodexUsageSyncService.instances = []
    monkeypatch.setattr(codex_usage, "CodexUsageSyncService", FakeCodexUsageSyncService)

    app = create_app(settings_for(tmp_path, glob_value=str(tmp_path / "*.jsonl")))
    with TestClient(app) as client:
        response = client.get("/api/usage/tokens/sync-codex/status")
        assert response.status_code == 200
        body = response.json()
        assert body["enabled"] is True
        assert body["running"] is True
        assert body["sessions_glob"] == str(tmp_path / "*.jsonl")
        assert body["poll_interval_seconds"] == 123
        assert body["last_discovered_files"] == 2


def write_jsonl(path: Path, *events: dict[str, Any]) -> None:
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n")


def session_meta(session_id: str) -> dict[str, Any]:
    return {
        "timestamp": "2026-07-02T12:00:00.000Z",
        "type": "session_meta",
        "payload": {
            "id": "codex-external-runtime",
            "cwd": f"/private/tmp/atc-agents/{session_id}",
        },
    }


def turn_context(session_id: str) -> dict[str, Any]:
    return {
        "timestamp": "2026-07-02T12:00:01.000Z",
        "type": "turn_context",
        "payload": {
            "model": "gpt-5.5",
            "cwd": f"/private/tmp/atc-agents/{session_id}",
        },
    }


def token_count(total_tokens: int = 25) -> dict[str, Any]:
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": 20,
                    "cached_input_tokens": 10,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 2,
                    "total_tokens": total_tokens,
                }
            },
        },
    }


def test_codex_sync_endpoint_records_tokens_and_summary_once(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    codex_dir = tmp_path / "codex"
    codex_dir.mkdir()
    settings = Settings(
        database={"path": str(db_path)},
        token_tracker={
            "poll_interval_seconds": 999,
            "codex_enabled": False,
            "codex_sessions_glob": str(codex_dir / "*.jsonl"),
        },
        backup={"auto_backup_enabled": False},
    )
    app = create_app(settings)

    with TestClient(app) as client:

        async def create_records() -> str:
            project = await db_ops.create_project(
                app.state.db,
                "runtime-codex-project",
                agent_provider="codex",
            )
            session = await db_ops.create_session(
                app.state.db,
                project.id,
                "ace",
                "ace",
                provider="codex",
                status="working",
            )
            return session.id

        session_id = asyncio.run(create_records())
        write_jsonl(
            codex_dir / "rollout-runtime.jsonl",
            session_meta(session_id),
            turn_context(session_id),
            token_count(),
        )

        first = client.post("/api/usage/tokens/sync-codex")
        assert first.status_code == 200
        assert first.json()["inserted_events"] == 1

        second = client.post("/api/usage/tokens/sync-codex")
        assert second.status_code == 200
        assert second.json()["inserted_events"] == 0

        summary = client.get("/api/usage/summary")
        assert summary.status_code == 200
        assert summary.json()["today_tokens"] >= 25
        assert summary.json()["month_tokens"] >= 25

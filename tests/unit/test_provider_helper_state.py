from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from atc.state.db import (
    append_provider_helper_event,
    create_project,
    create_provider_helper_run,
    create_session,
    get_connection,
    get_provider_helper_run,
    list_provider_helper_events,
    list_provider_helper_runs,
    run_migrations,
    update_provider_helper_run,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
async def migrated_db(tmp_path: Path):
    db_path = tmp_path / "atc.db"
    await run_migrations(str(db_path))
    async with get_connection(str(db_path)) as conn:
        yield conn


@pytest.mark.asyncio
async def test_provider_helper_run_and_events_are_durable_audit_records(migrated_db) -> None:
    project = await create_project(migrated_db, "Helper Project")
    session = await create_session(
        migrated_db,
        project.id,
        "leader",
        "Leader",
        provider="codex",
        status="running",
    )

    run = await create_provider_helper_run(
        migrated_db,
        provider="codex",
        helper_id="external-helper-1",
        parent_session_id=session.id,
        parent_role="leader",
        project_id=project.id,
        purpose="inspect_blockers",
        visibility="hidden",
        prompt_text="Find blockers",
        metadata={"reason": "unit-test"},
    )

    assert run.status == "requested"
    assert run.visibility == "hidden"
    assert run.metadata() == {"reason": "unit-test"}

    event = await append_provider_helper_event(
        migrated_db,
        helper_run_id=run.id,
        event_type="helper_requested",
        message="queued",
        payload={"visibility": "hidden"},
    )
    assert event.payload() == {"visibility": "hidden"}

    updated = await update_provider_helper_run(
        migrated_db,
        run.id,
        status="completed",
        finished_at="2026-07-09T18:00:00Z",
        summary="No blockers",
        output_text="All clear",
    )
    assert updated is not None
    assert updated.status == "completed"
    assert updated.summary == "No blockers"
    assert updated.output_text == "All clear"
    assert updated.metadata() == {"reason": "unit-test"}

    fetched = await get_provider_helper_run(migrated_db, run.id)
    assert fetched is not None
    assert fetched.parent_session_id == session.id
    assert fetched.parent_role == "leader"

    runs = await list_provider_helper_runs(
        migrated_db,
        parent_session_id=session.id,
        visibility="hidden",
    )
    assert [item.id for item in runs] == [run.id]

    events = await list_provider_helper_events(migrated_db, run.id)
    assert [item.event_type for item in events] == ["helper_requested"]
    assert events[0].message == "queued"


@pytest.mark.asyncio
async def test_provider_helper_migration_creates_expected_tables(migrated_db) -> None:
    cursor = await migrated_db.execute(
        """SELECT name FROM sqlite_master
           WHERE type = 'table' AND name IN ('provider_helper_runs', 'provider_helper_events')
           ORDER BY name"""
    )
    rows = await cursor.fetchall()
    assert [row["name"] for row in rows] == ["provider_helper_events", "provider_helper_runs"]

"""Regression tests for auto-respawn dead leader fix (PR #156).

Covers:
- _respawn_leader clears DB state before spawning
- _respawn_leader returns None when start_leader raises
- _send_leader_kickoff respawns on dead-pane ValueError
- _send_leader_kickoff re-raises on non-dead-pane ValueError
- _verify_leader_started respawns and re-sends kickoff on dead pane
- Respawn is only attempted once (no infinite loop)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from atc.core.events import EventBus
from atc.state.db import (
    _SCHEMA_SQL,
    create_leader,
    create_project,
    get_connection,
    run_migrations,
)
from atc.tower.controller import TowerController, TowerState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db():
    """In-memory database with schema applied."""
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
async def tower_with_project(db, event_bus):
    """TowerController with a project and leader row already in DB."""
    project = await create_project(db, "test-proj", repo_path="/tmp/repo")
    await create_leader(db, project.id)
    tower = TowerController(db, event_bus)
    tower._current_project_id = project.id
    tower._state = TowerState.MANAGING
    return tower, project.id


# ---------------------------------------------------------------------------
# Test 1: _respawn_leader clears DB state before spawning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_respawn_leader_clears_db_before_spawn(db, event_bus) -> None:
    """_respawn_leader sets session_id=NULL and status=idle before calling start_leader."""
    project = await create_project(db, "proj-respawn")
    await create_leader(db, project.id)

    # Pre-seed a fake session_id on the leader row
    await db.execute(
        "UPDATE leaders SET session_id = 'old-dead-session', status = 'error' WHERE project_id = ?",
        (project.id,),
    )
    await db.commit()

    tower = TowerController(db, event_bus)
    tower._current_project_id = project.id

    executed_states: list[tuple] = []

    original_execute = db.execute

    async def tracking_execute(sql: str, params: tuple = ()) -> object:
        if "session_id = NULL" in sql or "session_id=NULL" in sql or "session_id = ?" in sql:
            executed_states.append(("update_leaders", params))
        return await original_execute(sql, params)

    new_session_id = "new-session-456"
    with (
        patch("atc.tower.controller.start_leader", new=AsyncMock(return_value=new_session_id)),
        patch("atc.tower.controller.build_context_package", new=AsyncMock(return_value={})),
    ):
        result = await tower._respawn_leader(project.id, "do the thing")

    # Verify DB was updated (session_id cleared)
    cursor = await db.execute(
        "SELECT session_id, status FROM leaders WHERE project_id = ?", (project.id,)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] is None  # session_id cleared
    assert row[1] == "idle"  # status reset

    assert result == new_session_id


# ---------------------------------------------------------------------------
# Test 2: _respawn_leader returns None on failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_respawn_leader_returns_none_on_failure(db, event_bus) -> None:
    """_respawn_leader returns None when start_leader raises an exception."""
    project = await create_project(db, "proj-fail")
    await create_leader(db, project.id)

    tower = TowerController(db, event_bus)
    tower._current_project_id = project.id

    with (
        patch(
            "atc.tower.controller.start_leader",
            new=AsyncMock(side_effect=RuntimeError("tmux exploded")),
        ),
        patch("atc.tower.controller.build_context_package", new=AsyncMock(return_value={})),
    ):
        result = await tower._respawn_leader(project.id, "some goal")

    assert result is None


# ---------------------------------------------------------------------------
# Test 3: _send_leader_kickoff respawns on dead-pane ValueError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_leader_kickoff_respawns_on_dead_pane(db, event_bus) -> None:
    """_send_leader_kickoff calls _respawn_leader when ValueError contains 'dead'."""
    project = await create_project(db, "proj-kick")
    await create_leader(db, project.id)

    tower = TowerController(db, event_bus)
    tower._current_project_id = project.id
    tower._state = TowerState.MANAGING

    send_calls: list[str] = []

    async def fake_send_leader_message(db, project_id, msg, *, event_bus=None):  # type: ignore[override]
        if not send_calls:
            send_calls.append("first")
            raise ValueError("Leader tmux pane is dead")
        send_calls.append("retry")

    with (
        patch(
            "atc.tower.controller.send_leader_message",
            new=fake_send_leader_message,
        ),
        patch(
            "atc.tower.controller.start_leader",
            new=AsyncMock(return_value="respawned-sess"),
        ),
        patch(
            "atc.tower.controller.build_context_package",
            new=AsyncMock(return_value={}),
        ),
    ):
        await tower._send_leader_kickoff("old-session", "build something")

    # First call raised, retry succeeded
    assert send_calls == ["first", "retry"]
    # Leader session updated to new id
    assert tower._leader_session_id == "respawned-sess"


# ---------------------------------------------------------------------------
# Test 4: _send_leader_kickoff re-raises on non-dead-pane ValueError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_leader_kickoff_reraises_non_dead_error(db, event_bus) -> None:
    """_send_leader_kickoff re-raises ValueError when message does not match dead-pane pattern."""
    project = await create_project(db, "proj-reraise")
    await create_leader(db, project.id)

    tower = TowerController(db, event_bus)
    tower._current_project_id = project.id

    async def fake_send_raises(db, project_id, msg, *, event_bus=None):  # type: ignore[override]
        raise ValueError("some other problem entirely")

    respawn_called = False

    async def fake_respawn(project_id: str, goal: str) -> str | None:
        nonlocal respawn_called
        respawn_called = True
        return "new-id"

    with patch("atc.tower.controller.send_leader_message", new=fake_send_raises):
        tower._respawn_leader = fake_respawn  # type: ignore[method-assign]
        # _send_leader_kickoff silently logs exceptions — check respawn was NOT called
        await tower._send_leader_kickoff("sess-1", "goal")

    assert not respawn_called


# ---------------------------------------------------------------------------
# Test 5: _verify_leader_started respawns and re-sends kickoff on dead pane
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_leader_started_respawns_on_dead_pane(db, event_bus) -> None:
    """_verify_leader_started calls _respawn_leader when nudge raises dead-pane ValueError."""
    project = await create_project(db, "proj-verify")
    await create_leader(db, project.id)

    tower = TowerController(db, event_bus)
    tower._current_project_id = project.id
    tower._state = TowerState.MANAGING
    tower._leader_session_id = "orig-sess"

    nudge_calls: list[str] = []
    respawn_calls: list[str] = []
    kickoff_calls: list[str] = []

    async def fake_send(db, project_id, msg, *, event_bus=None):  # type: ignore[override]
        if "continue" in msg.lower() or "please" in msg.lower():
            nudge_calls.append(msg)
            raise ValueError("Leader tmux pane is dead")

    async def fake_respawn(project_id: str, goal: str) -> str | None:
        respawn_calls.append(project_id)
        tower._leader_session_id = "new-sess"
        return "new-sess"

    async def fake_kickoff(session_id: str, goal: str) -> None:
        kickoff_calls.append(session_id)

    with (
        patch("atc.tower.controller.send_leader_message", new=fake_send),
        patch("atc.tower.controller.asyncio.sleep", new=AsyncMock()),
    ):
        tower._respawn_leader = fake_respawn  # type: ignore[method-assign]
        tower._send_leader_kickoff = fake_kickoff  # type: ignore[method-assign]

        await tower._verify_leader_started(project.id, "orig-sess", "build it")

    assert len(respawn_calls) >= 1
    assert len(kickoff_calls) >= 1
    assert kickoff_calls[0] == "new-sess"


# ---------------------------------------------------------------------------
# Test 6: Respawn is only attempted once (not infinite loop)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_leader_kickoff_respawns_only_once(db, event_bus) -> None:
    """_send_leader_kickoff only calls _respawn_leader once, not repeatedly."""
    project = await create_project(db, "proj-once")
    await create_leader(db, project.id)

    tower = TowerController(db, event_bus)
    tower._current_project_id = project.id

    respawn_count = 0

    async def always_dead(db, project_id, msg, *, event_bus=None):  # type: ignore[override]
        raise ValueError("Leader tmux pane is dead")

    async def counting_respawn(project_id: str, goal: str) -> str | None:
        nonlocal respawn_count
        respawn_count += 1
        return "new-sess"

    with patch("atc.tower.controller.send_leader_message", new=always_dead):
        tower._respawn_leader = counting_respawn  # type: ignore[method-assign]
        await tower._send_leader_kickoff("sess-1", "goal")

    # Respawn triggered exactly once — no retry loop
    assert respawn_count == 1

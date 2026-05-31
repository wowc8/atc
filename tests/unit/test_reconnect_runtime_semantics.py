from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from atc.session.reconnect import reconnect_session
from atc.session.state_machine import SessionStatus
from atc.state.db import _SCHEMA_SQL, create_project, create_session, get_connection, get_session


@pytest.fixture
async def db():
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.mark.asyncio
async def test_reconnect_refuses_reuse_on_provider_mismatch(db) -> None:
    project = await create_project(db, "proj")
    session = await create_session(
        db,
        project_id=project.id,
        session_type="ace",
        name="ace-test",
        provider="codex",
        status=SessionStatus.IDLE.value,
    )
    await db.commit()
    with (
        patch("atc.session.reconnect._pane_is_alive", new_callable=AsyncMock, return_value=False),
        patch("atc.config.load_settings") as mock_load_settings,
    ):
        mock_load_settings.return_value.agent_provider.default = "claude_code"
        ok = await reconnect_session(db, session.id)

    assert ok is False
    refreshed = await get_session(db, session.id)
    assert refreshed is not None
    assert refreshed.status == SessionStatus.DISCONNECTED.value


@pytest.mark.asyncio
async def test_reconnect_respawns_when_provider_matches(db) -> None:
    project = await create_project(db, "proj")
    session = await create_session(
        db,
        project_id=project.id,
        session_type="ace",
        name="ace-test",
        provider="claude_code",
        status=SessionStatus.IDLE.value,
    )
    await db.commit()
    with (
        patch("atc.session.reconnect._pane_is_alive", new_callable=AsyncMock, return_value=False),
        patch("atc.config.load_settings") as mock_load_settings,
        patch(
            "atc.session.reconnect._spawn_provider_session",
            new_callable=AsyncMock,
            return_value=("atc", "%atc:0.55"),
        ),
        patch("atc.session.reconnect.transition", new_callable=AsyncMock),
        patch("atc.session.reconnect.db_ops.update_session_tmux", new_callable=AsyncMock),
    ):
        mock_load_settings.return_value.agent_provider.default = "claude_code"
        ok = await reconnect_session(db, session.id)

    assert ok is True



@pytest.mark.asyncio
async def test_reconnect_uses_stamped_provider_even_if_current_default_changed(db) -> None:
    project = await create_project(db, "proj")
    session = await create_session(
        db,
        project_id=project.id,
        session_type="ace",
        name="sticky-provider-ace",
        provider="codex",
        status=SessionStatus.IDLE.value,
    )
    await db.commit()
    with (
        patch("atc.session.reconnect._pane_is_alive", new_callable=AsyncMock, return_value=False),
        patch("atc.config.load_settings") as mock_load_settings,
        patch(
            "atc.session.reconnect._spawn_provider_session",
            new_callable=AsyncMock,
            return_value=("atc", "%atc:0.77"),
        ) as mock_spawn,
        patch("atc.session.reconnect.transition", new_callable=AsyncMock),
        patch("atc.session.reconnect.db_ops.update_session_tmux", new_callable=AsyncMock),
    ):
        mock_load_settings.return_value.agent_provider.default = "codex"
        ok = await reconnect_session(db, session.id)

    assert ok is True
    assert mock_spawn.await_count == 1
    args, kwargs = mock_spawn.await_args
    assert args[1] == session.id
    assert kwargs["session_type"] == "ace"

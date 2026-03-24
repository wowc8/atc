"""Tests for tower session reuse on restart (Issue #126)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from atc.session.state_machine import SessionStatus
from atc.state.db import (
    _SCHEMA_SQL,
    create_project,
    create_session,
    get_connection,
    get_session,
    run_migrations,
    update_session_status,
)
from atc.tower.session import _DEFAULT_STAGING_ROOT, start_tower_session


@pytest.fixture
async def db():
    """In-memory database with schema applied."""
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.mark.asyncio
async def test_reuses_most_recent_disconnected_session_with_valid_staging_dir(
    db, tmp_path: Path
) -> None:
    """After all tower sessions are disconnected, start_tower_session should reuse
    the most recent one that has a valid CLAUDE.md in its staging dir."""
    project = await create_project(db, "test-proj")

    # Create a disconnected tower session
    sess = await create_session(
        db,
        project_id=project.id,
        session_type="tower",
        name="tower-test",
        status=SessionStatus.DISCONNECTED.value,
    )
    await db.commit()

    # Place a CLAUDE.md in the staging directory
    staging_dir = Path(tmp_path) / sess.id
    staging_dir.mkdir(parents=True)
    (staging_dir / "CLAUDE.md").write_text("# Tower\n")

    mock_pane_id = "%atc:0.99"

    with (
        patch(
            "atc.tower.session._DEFAULT_STAGING_ROOT",
            str(tmp_path),
        ),
        patch("atc.tower.session._ensure_tmux_session", new_callable=AsyncMock),
        patch(
            "atc.tower.session._spawn_pane",
            new_callable=AsyncMock,
            return_value=mock_pane_id,
        ),
        patch("atc.tower.session._accept_trust_dialog", new_callable=AsyncMock),
        patch("atc.tower.session._pane_is_alive", new_callable=AsyncMock, return_value=False),
        patch("atc.tower.session.get_launch_command", return_value="claude"),
        patch("atc.tower.session.transition", new_callable=AsyncMock),
        patch("atc.tower.session.db_ops.update_session_tmux", new_callable=AsyncMock),
    ):
        returned_id = await start_tower_session(db, project.id)

    # Must reuse the existing session, not create a new row
    assert returned_id == sess.id

    # Confirm no extra tower sessions were created
    cursor = await db.execute(
        "SELECT COUNT(*) FROM sessions WHERE session_type = 'tower'"
    )
    row = await cursor.fetchone()
    assert row[0] == 1


@pytest.mark.asyncio
async def test_creates_new_session_when_no_valid_staging_dir(db, tmp_path: Path) -> None:
    """When no disconnected session has a valid CLAUDE.md, a new session is created."""
    project = await create_project(db, "test-proj")

    # Create a disconnected tower session but NO staging dir
    await create_session(
        db,
        project_id=project.id,
        session_type="tower",
        name="tower-test",
        status=SessionStatus.DISCONNECTED.value,
    )
    await db.commit()

    mock_pane_id = "%atc:0.99"

    with (
        patch(
            "atc.tower.session._DEFAULT_STAGING_ROOT",
            str(tmp_path),
        ),
        patch("atc.tower.session._ensure_tmux_session", new_callable=AsyncMock),
        patch(
            "atc.tower.session._spawn_pane",
            new_callable=AsyncMock,
            return_value=mock_pane_id,
        ),
        patch("atc.tower.session._accept_trust_dialog", new_callable=AsyncMock),
        patch("atc.tower.session._pane_is_alive", new_callable=AsyncMock, return_value=False),
        patch("atc.tower.session.get_launch_command", return_value="claude"),
        patch("atc.tower.session.transition", new_callable=AsyncMock),
        patch("atc.tower.session.db_ops.update_session_tmux", new_callable=AsyncMock),
        patch("atc.tower.session.deploy_tower_files") as mock_deploy,
    ):
        mock_root = tmp_path / "new-sess"
        mock_root.mkdir()
        (mock_root / "CLAUDE.md").write_text("# Tower\n")
        mock_deployed = AsyncMock()
        mock_deployed.root = mock_root
        mock_deploy.return_value = mock_deployed

        returned_id = await start_tower_session(db, project.id)

    # Should be a NEW session (different from the disconnected one)
    cursor = await db.execute(
        "SELECT COUNT(*) FROM sessions WHERE session_type = 'tower'"
    )
    row = await cursor.fetchone()
    assert row[0] == 2  # original disconnected + new one

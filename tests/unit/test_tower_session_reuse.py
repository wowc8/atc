"""Tests for tower session reuse on restart (Issue #126)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from atc.session.state_machine import SessionStatus
from atc.state.db import _SCHEMA_SQL, create_project, create_session, get_connection
from atc.tower.session import start_tower_session


@pytest.fixture
async def db():
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.mark.asyncio
async def test_reuses_live_same_provider_tower_session_with_valid_staging_dir(
    db, tmp_path: Path
) -> None:
    project = await create_project(db, "test-proj")
    sess = await create_session(
        db,
        project_id=project.id,
        session_type="tower",
        name="tower-test",
        provider="codex",
        status=SessionStatus.IDLE.value,
    )
    await db.execute("UPDATE sessions SET tmux_pane = ? WHERE id = ?", ("%existing-pane", sess.id))
    await db.commit()

    staging_dir = Path(tmp_path) / sess.id
    staging_dir.mkdir(parents=True)
    (staging_dir / "CLAUDE.md").write_text("# Tower\n")
    (staging_dir / "AGENTS.md").write_text("# Tower\n")

    with (
        patch("atc.tower.session._DEFAULT_STAGING_ROOT", str(tmp_path)),
        patch("atc.tower.session._pane_is_alive", new_callable=AsyncMock, return_value=True),
    ):
        returned_id = await start_tower_session(db, project.id)

    assert returned_id == sess.id


@pytest.mark.asyncio
async def test_does_not_reuse_live_tower_session_when_provider_mismatches(
    db, tmp_path: Path
) -> None:
    project = await create_project(db, "test-proj")
    sess = await create_session(
        db,
        project_id=project.id,
        session_type="tower",
        name="tower-test",
        provider="codex",
        status=SessionStatus.IDLE.value,
    )
    await db.commit()

    staging_dir = Path(tmp_path) / sess.id
    staging_dir.mkdir(parents=True)
    (staging_dir / "CLAUDE.md").write_text("# Tower\n")
    (staging_dir / "AGENTS.md").write_text("# Tower\n")

    mock_root = tmp_path / "new-sess"
    mock_root.mkdir()
    (mock_root / "CLAUDE.md").write_text("# Tower\n")
    (mock_root / "AGENTS.md").write_text("# Tower\n")
    mock_deployed = Mock()
    mock_deployed.root = mock_root
    mock_deployed.claude_md_path = mock_root / "CLAUDE.md"

    with (
        patch("atc.tower.session._DEFAULT_STAGING_ROOT", str(tmp_path)),
        patch("atc.tower.session._pane_is_alive", new_callable=AsyncMock, return_value=True),
        patch("atc.tower.session._kill_pane", new_callable=AsyncMock),
        patch(
            "atc.tower.session._spawn_provider_session",
            new_callable=AsyncMock,
            return_value=("atc", "%atc:0.99"),
        ),
        patch("atc.tower.session.transition", new_callable=AsyncMock),
        patch("atc.tower.session.db_ops.update_session_tmux", new_callable=AsyncMock),
        patch("atc.tower.session.deploy_tower_files", return_value=mock_deployed),
    ):
        returned_id = await start_tower_session(db, project.id)

    assert returned_id != sess.id
    cursor = await db.execute("SELECT COUNT(*) FROM sessions WHERE session_type = 'tower'")
    row = await cursor.fetchone()
    assert row[0] == 2


@pytest.mark.asyncio
async def test_creates_new_session_when_no_valid_staging_dir(db, tmp_path: Path) -> None:
    project = await create_project(db, "test-proj")
    await create_session(
        db,
        project_id=project.id,
        session_type="tower",
        name="tower-test",
        status=SessionStatus.DISCONNECTED.value,
    )
    await db.commit()

    mock_root = tmp_path / "new-sess"
    mock_root.mkdir()
    (mock_root / "CLAUDE.md").write_text("# Tower\n")
    (mock_root / "AGENTS.md").write_text("# Tower\n")
    mock_deployed = Mock()
    mock_deployed.root = mock_root
    mock_deployed.claude_md_path = mock_root / "CLAUDE.md"

    with (
        patch("atc.tower.session._DEFAULT_STAGING_ROOT", str(tmp_path)),
        patch(
            "atc.tower.session._spawn_provider_session",
            new_callable=AsyncMock,
            return_value=("atc", "%atc:0.99"),
        ),
        patch("atc.tower.session._pane_is_alive", new_callable=AsyncMock, return_value=False),
        patch("atc.tower.session.transition", new_callable=AsyncMock),
        patch("atc.tower.session.db_ops.update_session_tmux", new_callable=AsyncMock),
        patch("atc.tower.session.deploy_tower_files", return_value=mock_deployed),
    ):
        returned_id = await start_tower_session(db, project.id)

    cursor = await db.execute("SELECT COUNT(*) FROM sessions WHERE session_type = 'tower'")
    row = await cursor.fetchone()
    assert row[0] == 2
    assert returned_id is not None



@pytest.mark.asyncio
async def test_mismatch_reuse_marks_old_tower_disconnected(db, tmp_path: Path) -> None:
    project = await create_project(db, "test-proj")
    sess = await create_session(
        db,
        project_id=project.id,
        session_type="tower",
        name="tower-test",
        provider="claude_code",
        status=SessionStatus.IDLE.value,
    )
    await db.execute("UPDATE sessions SET tmux_pane = ? WHERE id = ?", ("%existing-pane", sess.id))
    await db.commit()

    staging_dir = Path(tmp_path) / sess.id
    staging_dir.mkdir(parents=True)
    (staging_dir / "CLAUDE.md").write_text("# Tower\n")
    (staging_dir / "AGENTS.md").write_text("# Tower\n")

    mock_root = tmp_path / "new-sess"
    mock_root.mkdir()
    (mock_root / "CLAUDE.md").write_text("# Tower\n")
    (mock_root / "AGENTS.md").write_text("# Tower\n")
    mock_deployed = Mock()
    mock_deployed.root = mock_root
    mock_deployed.claude_md_path = mock_root / "CLAUDE.md"

    with (
        patch("atc.tower.session._DEFAULT_STAGING_ROOT", str(tmp_path)),
        patch("atc.tower.session._pane_is_alive", new_callable=AsyncMock, return_value=True),
        patch("atc.tower.session._kill_pane", new_callable=AsyncMock),
        patch(
            "atc.tower.session._spawn_provider_session",
            new_callable=AsyncMock,
            return_value=("atc", "%atc:0.99"),
        ),
        patch("atc.tower.session.transition", new_callable=AsyncMock),
        patch("atc.tower.session.db_ops.update_session_tmux", new_callable=AsyncMock),
        patch("atc.tower.session.deploy_tower_files", return_value=mock_deployed),
    ):
        returned_id = await start_tower_session(db, project.id)

    assert returned_id != sess.id
    cursor = await db.execute("SELECT status FROM sessions WHERE id = ?", (sess.id,))
    row = await cursor.fetchone()
    assert row[0] == SessionStatus.DISCONNECTED.value

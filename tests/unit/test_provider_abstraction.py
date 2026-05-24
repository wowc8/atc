"""Unit tests for the provider abstraction wiring (issue #135).

Covers:
- ClaudeCodeProvider.prepare_workspace: creates dirs, copies context file
- ClaudeCodeProvider.is_ready: returns True when pane shows ❯ prompt
- ClaudeCodeProvider.handle_startup: Claude startup dialogs are provider-owned
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from atc.agents.claude_provider import ClaudeCodeProvider


class TestPrepareWorkspace:
    @pytest.mark.asyncio
    async def test_creates_working_dir(self, tmp_path: Path) -> None:
        provider = ClaudeCodeProvider()
        target = tmp_path / "new_dir" / "nested"

        await provider.prepare_workspace("sess-1", working_dir=str(target))

        assert target.is_dir()

    @pytest.mark.asyncio
    async def test_copies_context_file(self, tmp_path: Path) -> None:
        provider = ClaudeCodeProvider()
        context_file = tmp_path / "src_CLAUDE.md"
        context_file.write_text("# Leader instructions\n")

        working_dir = tmp_path / "workspace"

        await provider.prepare_workspace(
            "sess-2",
            working_dir=str(working_dir),
            context_file=context_file,
        )

        dest = working_dir / "CLAUDE.md"
        assert dest.exists()
        assert dest.read_text() == "# Leader instructions\n"

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing_claude_md(self, tmp_path: Path) -> None:
        provider = ClaudeCodeProvider()
        working_dir = tmp_path / "workspace"
        working_dir.mkdir()

        existing = working_dir / "CLAUDE.md"
        existing.write_text("# Existing instructions\n")

        context_file = tmp_path / "new_CLAUDE.md"
        context_file.write_text("# New instructions\n")

        await provider.prepare_workspace(
            "sess-3",
            working_dir=str(working_dir),
            context_file=context_file,
        )

        assert existing.read_text() == "# Existing instructions\n"

    @pytest.mark.asyncio
    async def test_no_context_file_still_creates_dir(self, tmp_path: Path) -> None:
        provider = ClaudeCodeProvider()
        target = tmp_path / "just_dir"

        await provider.prepare_workspace("sess-4", working_dir=str(target))

        assert target.is_dir()
        assert not (target / "CLAUDE.md").exists()


class TestIsReady:
    @pytest.mark.asyncio
    async def test_returns_false_for_unknown_session(self) -> None:
        provider = ClaudeCodeProvider()
        result = await provider.is_ready("does-not-exist")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_prompt_detected(self) -> None:
        provider = ClaudeCodeProvider()

        from atc.agents.base import SessionStatus
        from atc.agents.claude_provider import _TrackedSession

        provider._sessions["sess-ready"] = _TrackedSession(
            session_id="sess-ready",
            pane_id="%42",
            status=SessionStatus.IDLE,
        )

        async def _fake_subproc(*args: str, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            if "display-message" in args:
                proc.communicate = AsyncMock(return_value=(b"0\n", b""))
            else:
                proc.communicate = AsyncMock(return_value=(b"some output\n>\n", b""))
            proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_subproc):
            result = await provider.is_ready("sess-ready")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_tui_fullscreen(self) -> None:
        provider = ClaudeCodeProvider()

        from atc.agents.base import SessionStatus
        from atc.agents.claude_provider import _TrackedSession

        provider._sessions["sess-tui"] = _TrackedSession(
            session_id="sess-tui",
            pane_id="%99",
            status=SessionStatus.STARTING,
        )

        async def _always_alt_on(*args: str, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"1\n", b""))
            proc.returncode = 0
            return proc

        with (
            patch("asyncio.create_subprocess_exec", side_effect=_always_alt_on),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_sleep.side_effect = None
            result = await provider.is_ready("sess-tui")

        assert result is False


class TestHandleStartup:
    @pytest.mark.asyncio
    async def test_calls_claude_runtime_startup_handler(self) -> None:
        provider = ClaudeCodeProvider()

        from atc.agents.base import SessionStatus
        from atc.agents.claude_provider import _TrackedSession

        provider._sessions["sess-start"] = _TrackedSession(
            session_id="sess-start",
            pane_id="%77",
            status=SessionStatus.STARTING,
        )

        with patch("atc.agents.claude_provider.accept_startup_dialogs", new_callable=AsyncMock) as mock_startup:
            await provider.handle_startup("sess-start")

        mock_startup.assert_awaited_once()

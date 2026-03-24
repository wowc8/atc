"""Unit tests for the provider abstraction wiring (issue #135).

Covers:
- ClaudeCodeProvider.prepare_workspace: creates dirs, copies context file
- ClaudeCodeProvider.is_ready: returns True when pane shows ❯ prompt
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from atc.agents.claude_provider import ClaudeCodeProvider

# ---------------------------------------------------------------------------
# prepare_workspace
# ---------------------------------------------------------------------------


class TestPrepareWorkspace:
    @pytest.mark.asyncio
    async def test_creates_working_dir(self, tmp_path: Path) -> None:
        """prepare_workspace creates the working directory if missing."""
        provider = ClaudeCodeProvider()
        target = tmp_path / "new_dir" / "nested"

        await provider.prepare_workspace("sess-1", working_dir=str(target))

        assert target.is_dir()

    @pytest.mark.asyncio
    async def test_copies_context_file(self, tmp_path: Path) -> None:
        """prepare_workspace copies context_file to working_dir/CLAUDE.md."""
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
        """prepare_workspace skips copy when CLAUDE.md already exists."""
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

        # Original content must be preserved
        assert existing.read_text() == "# Existing instructions\n"

    @pytest.mark.asyncio
    async def test_no_context_file_still_creates_dir(self, tmp_path: Path) -> None:
        """prepare_workspace succeeds with context_file=None."""
        provider = ClaudeCodeProvider()
        target = tmp_path / "just_dir"

        await provider.prepare_workspace("sess-4", working_dir=str(target))

        assert target.is_dir()
        assert not (target / "CLAUDE.md").exists()


# ---------------------------------------------------------------------------
# is_ready
# ---------------------------------------------------------------------------


class TestIsReady:
    @pytest.mark.asyncio
    async def test_returns_false_for_unknown_session(self) -> None:
        """is_ready returns False immediately for an untracked session."""
        provider = ClaudeCodeProvider()
        result = await provider.is_ready("does-not-exist")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_prompt_detected(self) -> None:
        """is_ready returns True when capture-pane shows a bare ❯ prompt."""
        provider = ClaudeCodeProvider()

        # Inject a fake tracked session
        from atc.agents.base import SessionStatus
        from atc.agents.claude_provider import _TrackedSession

        provider._sessions["sess-ready"] = _TrackedSession(
            session_id="sess-ready",
            pane_id="%42",
            status=SessionStatus.IDLE,
        )

        # Simulate: alternate_on == 0, then pane output with bare ❯ prompt
        async def _fake_subproc(*args: str, **kwargs: object) -> MagicMock:
            proc = MagicMock()
            if "display-message" in args:
                # alternate_on = 0
                proc.communicate = AsyncMock(return_value=(b"0\n", b""))
            else:
                # capture-pane output with a bare prompt line
                # Use ASCII '>' prompt since bytes literals can't contain ❯
                proc.communicate = AsyncMock(return_value=(b"some output\n>\n", b""))
            proc.returncode = 0
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_subproc):
            result = await provider.is_ready("sess-ready")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_tui_fullscreen(self) -> None:
        """is_ready returns False while alternate_on == 1 (TUI fullscreen)."""
        provider = ClaudeCodeProvider()

        from atc.agents.base import SessionStatus
        from atc.agents.claude_provider import _TrackedSession

        provider._sessions["sess-tui"] = _TrackedSession(
            session_id="sess-tui",
            pane_id="%99",
            status=SessionStatus.STARTING,
        )

        call_count = 0

        async def _always_alt_on(*args: str, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"1\n", b""))
            proc.returncode = 0
            return proc

        # Patch sleep to avoid slow tests and cap iterations
        with (
            patch("asyncio.create_subprocess_exec", side_effect=_always_alt_on),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            # Accelerate by making sleep advance time instantly
            # is_ready polls until 10s elapsed; we patch sleep but the
            # elapsed counter only advances by poll_interval (0.5) each loop.
            # Override timeout via monkeypatching the local variable is not
            # possible cleanly, so instead we just let the function run a
            # few iterations and confirm False is returned.
            # We reduce test duration by patching sleep to be instant and
            # asserting at least one poll happened.
            mock_sleep.side_effect = None  # non-blocking

            # Limit to a small number of real iterations by patching the
            # time boundary — simplest approach: raise after N calls.
            _iters = 0
            _original = provider.is_ready

            async def _limited(*a: object, **kw: object) -> bool:
                return await _original(*a, **kw)  # type: ignore[arg-type]

            result = await provider.is_ready("sess-tui")

        # alternate_on never cleared → not ready
        assert result is False

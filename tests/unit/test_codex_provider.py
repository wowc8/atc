from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atc.agents.base import SessionStatus
from atc.agents.codex_provider import CodexProvider


def _make_process(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> AsyncMock:
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


class TestCodexProvider:
    @pytest.fixture
    def provider(self) -> CodexProvider:
        return CodexProvider(tmux_session="test-atc")

    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("asyncio.create_subprocess_exec")
    async def test_spawn_session(
        self,
        mock_exec: AsyncMock,
        _mock_which: MagicMock,
        provider: CodexProvider,
    ) -> None:
        mock_exec.return_value = _make_process(stdout=b"%42\n")
        info = await provider.spawn_session("worker-1", working_dir="/tmp/repo")
        assert info.session_id == "worker-1"
        assert info.status == SessionStatus.IDLE
        assert info.metadata["pane_id"] == "%42"

    @patch("shutil.which", return_value=None)
    async def test_spawn_no_tmux_raises(
        self,
        _mock_which: MagicMock,
        provider: CodexProvider,
    ) -> None:
        with pytest.raises(Exception, match="tmux"):
            await provider.spawn_session("s1")

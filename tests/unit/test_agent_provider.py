"""Unit tests for the agent provider abstraction layer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atc.agents.base import (
    AgentProvider,
    OutputChunk,
    PromptResult,
    ProviderError,
    SessionInfo,
    SessionStatus,
)
from atc.agents.claude_provider import ClaudeCodeProvider
from atc.agents.factory import (
    _REGISTRY,
    create_provider,
    get_provider_class,
    list_providers,
    register_provider,
)
from atc.agents.opencode_provider import OpenCodeProvider

# ---------------------------------------------------------------------------
# Base protocol / data classes
# ---------------------------------------------------------------------------


class TestSessionStatus:
    def test_all_values_exist(self) -> None:
        assert SessionStatus.STARTING.value == "starting"
        assert SessionStatus.IDLE.value == "idle"
        assert SessionStatus.BUSY.value == "busy"
        assert SessionStatus.ERROR.value == "error"
        assert SessionStatus.STOPPED.value == "stopped"


class TestSessionInfo:
    def test_frozen(self) -> None:
        info = SessionInfo(session_id="s1", status=SessionStatus.IDLE)
        with pytest.raises(AttributeError):
            info.session_id = "other"  # type: ignore[misc]

    def test_default_metadata(self) -> None:
        info = SessionInfo(session_id="s1", status=SessionStatus.IDLE)
        assert info.metadata == {}

    def test_with_metadata(self) -> None:
        info = SessionInfo(
            session_id="s1",
            status=SessionStatus.BUSY,
            metadata={"pane_id": "%5"},
        )
        assert info.metadata["pane_id"] == "%5"


class TestPromptResult:
    def test_accepted(self) -> None:
        result = PromptResult(session_id="s1", accepted=True)
        assert result.accepted is True
        assert result.message == ""

    def test_rejected_with_message(self) -> None:
        result = PromptResult(session_id="s1", accepted=False, message="pane dead")
        assert result.accepted is False
        assert "pane dead" in result.message


class TestOutputChunk:
    def test_defaults(self) -> None:
        chunk = OutputChunk(session_id="s1", content="hello")
        assert chunk.is_final is False
        assert chunk.metadata == {}

    def test_final(self) -> None:
        chunk = OutputChunk(session_id="s1", content="done", is_final=True)
        assert chunk.is_final is True


class TestProviderError:
    def test_message_includes_provider(self) -> None:
        err = ProviderError("opencode", "connection refused")
        assert "opencode" in str(err)
        assert "connection refused" in str(err)
        assert err.provider == "opencode"


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    def test_claude_code_is_agent_provider(self) -> None:
        assert isinstance(ClaudeCodeProvider(), AgentProvider)

    def test_opencode_is_agent_provider(self) -> None:
        assert isinstance(OpenCodeProvider(), AgentProvider)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestFactory:
    def test_builtin_providers_registered(self) -> None:
        names = list_providers()
        assert "claude_code" in names
        assert "opencode" in names

    def test_create_claude_code(self) -> None:
        provider = create_provider("claude_code")
        assert provider.name == "claude_code"

    def test_create_opencode(self) -> None:
        provider = create_provider("opencode", base_url="http://localhost:9999")
        assert provider.name == "opencode"

    def test_create_unknown_raises(self) -> None:
        with pytest.raises(ProviderError, match="Unknown provider"):
            create_provider("nonexistent")

    def test_register_custom_provider(self) -> None:
        class CustomProvider:
            @property
            def name(self) -> str:
                return "custom"

        register_provider("custom", CustomProvider)  # type: ignore[arg-type]
        assert "custom" in list_providers()
        assert get_provider_class("custom") is CustomProvider

        # Clean up
        del _REGISTRY["custom"]

    def test_get_provider_class_none_for_unknown(self) -> None:
        assert get_provider_class("does_not_exist") is None


# ---------------------------------------------------------------------------
# ClaudeCodeProvider
# ---------------------------------------------------------------------------


def _make_process(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> AsyncMock:
    """Create a mock asyncio subprocess."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


class TestClaudeCodeProvider:
    @pytest.fixture
    def provider(self) -> ClaudeCodeProvider:
        return ClaudeCodeProvider(tmux_session="test-atc")

    async def test_name(self, provider: ClaudeCodeProvider) -> None:
        assert provider.name == "claude_code"

    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("asyncio.create_subprocess_exec")
    async def test_spawn_session(
        self,
        mock_exec: AsyncMock,
        _mock_which: MagicMock,
        provider: ClaudeCodeProvider,
    ) -> None:
        mock_exec.return_value = _make_process(stdout=b"%42\n")

        info = await provider.spawn_session("worker-1", working_dir="/tmp/repo")

        assert info.session_id == "worker-1"
        assert info.status == SessionStatus.IDLE
        assert info.metadata["pane_id"] == "%42"

    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("asyncio.create_subprocess_exec")
    async def test_spawn_duplicate_raises(
        self,
        mock_exec: AsyncMock,
        _mock_which: MagicMock,
        provider: ClaudeCodeProvider,
    ) -> None:
        mock_exec.return_value = _make_process(stdout=b"%42\n")
        await provider.spawn_session("s1")

        with pytest.raises(ProviderError, match="already exists"):
            await provider.spawn_session("s1")

    @patch("shutil.which", return_value=None)
    async def test_spawn_no_tmux_raises(
        self,
        _mock_which: MagicMock,
        provider: ClaudeCodeProvider,
    ) -> None:
        with pytest.raises(ProviderError, match="tmux"):
            await provider.spawn_session("s1")

    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("asyncio.create_subprocess_exec")
    async def test_send_prompt(
        self,
        mock_exec: AsyncMock,
        _mock_which: MagicMock,
        provider: ClaudeCodeProvider,
    ) -> None:
        # Spawn first
        mock_exec.return_value = _make_process(stdout=b"%42\n")
        await provider.spawn_session("s1")

        # Send prompt
        mock_exec.return_value = _make_process()
        result = await provider.send_prompt("s1", "Write hello world")

        assert result.accepted is True
        assert result.session_id == "s1"

    async def test_send_prompt_unknown_session(self, provider: ClaudeCodeProvider) -> None:
        with pytest.raises(ProviderError, match="not found"):
            await provider.send_prompt("unknown", "test")

    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("asyncio.create_subprocess_exec")
    async def test_get_status(
        self,
        mock_exec: AsyncMock,
        _mock_which: MagicMock,
        provider: ClaudeCodeProvider,
    ) -> None:
        # Spawn
        mock_exec.return_value = _make_process(stdout=b"%42\n")
        await provider.spawn_session("s1")

        # Check status (has-session succeeds)
        mock_exec.return_value = _make_process()
        info = await provider.get_status("s1")
        assert info.status == SessionStatus.IDLE

    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("asyncio.create_subprocess_exec")
    async def test_get_status_pane_dead(
        self,
        mock_exec: AsyncMock,
        _mock_which: MagicMock,
        provider: ClaudeCodeProvider,
    ) -> None:
        mock_exec.return_value = _make_process(stdout=b"%42\n")
        await provider.spawn_session("s1")

        # has-session returns non-zero
        mock_exec.return_value = _make_process(returncode=1)
        info = await provider.get_status("s1")
        assert info.status == SessionStatus.STOPPED

    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("asyncio.create_subprocess_exec")
    async def test_stop_session(
        self,
        mock_exec: AsyncMock,
        _mock_which: MagicMock,
        provider: ClaudeCodeProvider,
    ) -> None:
        mock_exec.return_value = _make_process(stdout=b"%42\n")
        await provider.spawn_session("s1")

        mock_exec.return_value = _make_process()
        await provider.stop_session("s1")

        sessions = await provider.list_sessions()
        assert len(sessions) == 0

    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("asyncio.create_subprocess_exec")
    async def test_list_sessions(
        self,
        mock_exec: AsyncMock,
        _mock_which: MagicMock,
        provider: ClaudeCodeProvider,
    ) -> None:
        mock_exec.return_value = _make_process(stdout=b"%1\n")
        await provider.spawn_session("s1")
        mock_exec.return_value = _make_process(stdout=b"%2\n")
        await provider.spawn_session("s2")

        sessions = await provider.list_sessions()
        assert len(sessions) == 2
        ids = {s.session_id for s in sessions}
        assert ids == {"s1", "s2"}

    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("asyncio.create_subprocess_exec")
    async def test_stream_output(
        self,
        mock_exec: AsyncMock,
        _mock_which: MagicMock,
        provider: ClaudeCodeProvider,
    ) -> None:
        mock_exec.return_value = _make_process(stdout=b"%42\n")
        await provider.spawn_session("s1")

        # capture-pane output
        mock_exec.return_value = _make_process(stdout=b"Hello from Claude\n")

        chunks: list[OutputChunk] = []
        async for chunk in provider.stream_output("s1"):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert "Hello from Claude" in chunks[0].content
        assert chunks[0].is_final is True


# ---------------------------------------------------------------------------
# OpenCodeProvider
# ---------------------------------------------------------------------------


class TestOpenCodeProvider:
    @pytest.fixture
    def provider(self) -> OpenCodeProvider:
        return OpenCodeProvider(base_url="http://localhost:9999", tmux_session="test-atc")

    async def test_name(self, provider: OpenCodeProvider) -> None:
        assert provider.name == "opencode"

    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("asyncio.create_subprocess_exec")
    async def test_spawn_session(
        self,
        mock_exec: AsyncMock,
        _mock_which: MagicMock,
        provider: OpenCodeProvider,
    ) -> None:
        # First call: API request (curl for POST /session)
        # Second call: tmux split-window
        api_response = b'{"id": "w1", "status": "idle"}'
        calls = [
            _make_process(stdout=api_response),  # curl POST /session
            _make_process(stdout=b"%50\n"),  # tmux split-window
        ]
        mock_exec.side_effect = calls

        info = await provider.spawn_session("w1", working_dir="/tmp/work")

        assert info.session_id == "w1"
        assert info.status == SessionStatus.IDLE

    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("asyncio.create_subprocess_exec")
    async def test_spawn_duplicate_raises(
        self,
        mock_exec: AsyncMock,
        _mock_which: MagicMock,
        provider: OpenCodeProvider,
    ) -> None:
        mock_exec.side_effect = [
            _make_process(stdout=b'{}'),
            _make_process(stdout=b"%50\n"),
        ]
        await provider.spawn_session("w1")

        with pytest.raises(ProviderError, match="already tracked"):
            await provider.spawn_session("w1")

    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("asyncio.create_subprocess_exec")
    async def test_send_prompt(
        self,
        mock_exec: AsyncMock,
        _mock_which: MagicMock,
        provider: OpenCodeProvider,
    ) -> None:
        # Spawn
        mock_exec.side_effect = [
            _make_process(stdout=b'{}'),
            _make_process(stdout=b"%50\n"),
        ]
        await provider.spawn_session("w1")

        # Reset side_effect so return_value works
        mock_exec.side_effect = None
        mock_exec.return_value = _make_process(stdout=b'{"ok": true}')
        result = await provider.send_prompt("w1", "Write tests")

        assert result.accepted is True
        assert result.session_id == "w1"

    async def test_send_prompt_unknown_session(self, provider: OpenCodeProvider) -> None:
        with pytest.raises(ProviderError, match="not tracked"):
            await provider.send_prompt("ghost", "test")

    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("asyncio.create_subprocess_exec")
    async def test_get_status(
        self,
        mock_exec: AsyncMock,
        _mock_which: MagicMock,
        provider: OpenCodeProvider,
    ) -> None:
        mock_exec.side_effect = [
            _make_process(stdout=b'{}'),
            _make_process(stdout=b"%50\n"),
        ]
        await provider.spawn_session("w1")

        # Reset side_effect so return_value works
        mock_exec.side_effect = None
        mock_exec.return_value = _make_process(
            stdout=b'{"id": "w1", "status": "busy"}'
        )
        info = await provider.get_status("w1")
        assert info.status == SessionStatus.BUSY

    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("asyncio.create_subprocess_exec")
    async def test_get_status_api_down(
        self,
        mock_exec: AsyncMock,
        _mock_which: MagicMock,
        provider: OpenCodeProvider,
    ) -> None:
        mock_exec.side_effect = [
            _make_process(stdout=b'{}'),
            _make_process(stdout=b"%50\n"),
        ]
        await provider.spawn_session("w1")

        # Reset side_effect so return_value works
        mock_exec.side_effect = None
        mock_exec.return_value = _make_process(returncode=1, stderr=b"Connection refused")
        info = await provider.get_status("w1")
        assert info.status == SessionStatus.ERROR

    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("asyncio.create_subprocess_exec")
    async def test_stop_session(
        self,
        mock_exec: AsyncMock,
        _mock_which: MagicMock,
        provider: OpenCodeProvider,
    ) -> None:
        mock_exec.side_effect = [
            _make_process(stdout=b'{}'),
            _make_process(stdout=b"%50\n"),
        ]
        await provider.spawn_session("w1")

        # DELETE /session/w1 then kill-pane
        mock_exec.side_effect = [
            _make_process(stdout=b'{}'),  # API delete
            _make_process(),  # kill-pane
        ]
        await provider.stop_session("w1")

        # After stop, session is removed locally; list_sessions calls API
        # which will fail (side_effect exhausted), returning empty
        mock_exec.side_effect = None
        mock_exec.return_value = _make_process(returncode=1, stderr=b"no mock")
        sessions = await provider.list_sessions()
        assert isinstance(sessions, list)

    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("asyncio.create_subprocess_exec")
    async def test_list_sessions(
        self,
        mock_exec: AsyncMock,
        _mock_which: MagicMock,
        provider: OpenCodeProvider,
    ) -> None:
        mock_exec.side_effect = [
            _make_process(stdout=b'{}'),
            _make_process(stdout=b"%50\n"),
        ]
        await provider.spawn_session("w1")

        # Reset side_effect so return_value works
        mock_exec.side_effect = None
        mock_exec.return_value = _make_process(
            stdout=b'{"sessions": [{"id": "w1", "status": "idle"}]}'
        )
        sessions = await provider.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].session_id == "w1"


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestConfigIntegration:
    def test_agent_provider_config_defaults(self) -> None:
        from atc.config import AgentProviderConfig

        cfg = AgentProviderConfig()
        assert cfg.default == "claude_code"
        assert cfg.opencode_url == "http://localhost:4096"
        assert cfg.tmux_session == "atc"

    def test_settings_has_agent_provider(self) -> None:
        from atc.config import Settings

        settings = Settings()
        assert settings.agent_provider.default == "claude_code"

    def test_create_provider_from_config(self) -> None:
        from atc.config import AgentProviderConfig

        cfg = AgentProviderConfig(default="opencode", opencode_url="http://localhost:5555")
        provider = create_provider(
            cfg.default,
            base_url=cfg.opencode_url,
            tmux_session=cfg.tmux_session,
        )
        assert provider.name == "opencode"

    def test_create_claude_from_config(self) -> None:
        from atc.config import AgentProviderConfig

        cfg = AgentProviderConfig(default="claude_code")
        provider = create_provider(
            cfg.default,
            tmux_session=cfg.tmux_session,
            claude_command=cfg.claude_command,
        )
        assert provider.name == "claude_code"

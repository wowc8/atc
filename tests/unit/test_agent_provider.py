"""Unit tests for the agent provider abstraction layer."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from atc.agents.base import (
    AgentProvider,
    CostModel,
    OutputChunk,
    PromptResult,
    ProviderCapabilities,
    ProviderError,
    ProviderMetadata,
    SessionInfo,
    SessionStatus,
)
from atc.agents.claude_provider import ClaudeCodeProvider
from atc.agents.factory import (
    _LAUNCH_COMMANDS,
    _METADATA,
    _REGISTRY,
    _SCANNED_DIRS,
    create_provider,
    get_launch_command,
    get_provider_class,
    get_provider_info,
    list_providers,
    load_plugins,
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


class TestProviderCapabilities:
    def test_capabilities_dataclass(self) -> None:
        caps = ProviderCapabilities(
            supports_streaming=True,
            supports_tool_use=True,
            context_window=200_000,
            model="test",
        )
        assert caps.supports_streaming is True
        assert caps.supports_tool_use is True
        assert caps.context_window == 200_000
        assert caps.model == "test"

    def test_capabilities_defaults(self) -> None:
        caps = ProviderCapabilities()
        assert caps.supports_streaming is False
        assert caps.context_window == 0

    def test_claude_capabilities(self) -> None:
        provider = ClaudeCodeProvider()
        caps = provider.get_capabilities()
        assert caps.supports_streaming is True
        assert caps.supports_tool_use is True
        assert caps.context_window == 200_000

    def test_opencode_capabilities(self) -> None:
        provider = OpenCodeProvider()
        caps = provider.get_capabilities()
        assert caps.supports_streaming is True
        assert caps.supports_tool_use is True
        assert caps.context_window == 200_000


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

        register_provider("custom", CustomProvider)
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
        # First call: ensure_server_running API check (GET /session)
        # Second call: API request (curl for POST /session)
        # Third call: tmux split-window
        calls = [
            _make_process(stdout=b'{"sessions": []}'),  # ensure_server GET /session
            _make_process(stdout=b'{"id": "w1", "status": "idle"}'),  # POST /session
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
            _make_process(stdout=b'{"sessions": []}'),  # ensure_server
            _make_process(stdout=b"{}"),  # POST /session
            _make_process(stdout=b"%50\n"),  # tmux split-window
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
            _make_process(stdout=b'{"sessions": []}'),  # ensure_server
            _make_process(stdout=b"{}"),  # POST /session
            _make_process(stdout=b"%50\n"),  # tmux split-window
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
            _make_process(stdout=b'{"sessions": []}'),  # ensure_server
            _make_process(stdout=b"{}"),
            _make_process(stdout=b"%50\n"),
        ]
        await provider.spawn_session("w1")

        # Reset side_effect so return_value works
        mock_exec.side_effect = None
        mock_exec.return_value = _make_process(stdout=b'{"id": "w1", "status": "busy"}')
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
            _make_process(stdout=b'{"sessions": []}'),  # ensure_server
            _make_process(stdout=b"{}"),
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
            _make_process(stdout=b'{"sessions": []}'),  # ensure_server
            _make_process(stdout=b"{}"),
            _make_process(stdout=b"%50\n"),
        ]
        await provider.spawn_session("w1")

        # DELETE /session/w1 then kill-pane
        mock_exec.side_effect = [
            _make_process(stdout=b"{}"),  # API delete
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
            _make_process(stdout=b'{"sessions": []}'),  # ensure_server
            _make_process(stdout=b"{}"),
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

    @patch("asyncio.create_subprocess_exec")
    async def test_ensure_server_running_already_up(
        self,
        mock_exec: AsyncMock,
        provider: OpenCodeProvider,
    ) -> None:
        """Server already running — no tmux session needed."""
        mock_exec.return_value = _make_process(stdout=b'{"sessions": []}')
        await provider.ensure_server_running()
        # Only one call to check the API, no tmux session creation
        assert mock_exec.call_count == 1

    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("asyncio.create_subprocess_exec")
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_ensure_server_running_starts_server(
        self,
        _mock_sleep: AsyncMock,
        mock_exec: AsyncMock,
        _mock_which: MagicMock,
        provider: OpenCodeProvider,
    ) -> None:
        """Server not running — starts in tmux and waits for it."""
        mock_exec.side_effect = [
            # First: API check fails (server not running)
            _make_process(returncode=1, stderr=b"Connection refused"),
            # Second: has-session check (session doesn't exist)
            _make_process(returncode=1, stderr=b"no session"),
            # Third: new-session to start opencode serve
            _make_process(returncode=0),
            # Fourth: retry API check succeeds
            _make_process(stdout=b'{"sessions": []}'),
        ]
        await provider.ensure_server_running()
        assert mock_exec.call_count == 4


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


# ---------------------------------------------------------------------------
# get_launch_command
# ---------------------------------------------------------------------------


class TestGetLaunchCommand:
    def test_claude_code_returns_claude_cmd(self) -> None:
        cmd = get_launch_command("claude_code")
        assert cmd == "claude --dangerously-skip-permissions"

    def test_opencode_returns_opencode_cmd(self) -> None:
        cmd = get_launch_command("opencode")
        assert cmd == "opencode"

    def test_unknown_falls_back_to_claude(self) -> None:
        cmd = get_launch_command("unknown_provider")
        assert cmd == "claude --dangerously-skip-permissions"

    def test_launch_commands_registry_has_both(self) -> None:
        assert "claude_code" in _LAUNCH_COMMANDS
        assert "opencode" in _LAUNCH_COMMANDS


class TestCostModel:
    def test_defaults(self) -> None:
        cost = CostModel()
        assert cost.input_cost_per_token == 0.0
        assert cost.currency == "USD"

    def test_custom(self) -> None:
        cost = CostModel(input_cost_per_token=0.003, output_cost_per_token=0.015, currency="EUR")
        assert cost.input_cost_per_token == 0.003
        assert cost.currency == "EUR"

    def test_frozen(self) -> None:
        cost = CostModel()
        with pytest.raises(AttributeError):
            cost.currency = "GBP"  # type: ignore[misc]


class TestProviderMetadataDataclass:
    def test_defaults(self) -> None:
        meta = ProviderMetadata(name="test")
        assert meta.version == "0.0.0"
        assert meta.author == ""

    def test_custom(self) -> None:
        meta = ProviderMetadata(name="x", version="2.1.0", description="A provider", author="Me")
        assert meta.version == "2.1.0"


class TestCapabilitiesWithCostModel:
    def test_none_by_default(self) -> None:
        assert ProviderCapabilities().cost_model is None

    def test_attached(self) -> None:
        caps = ProviderCapabilities(cost_model=CostModel(input_cost_per_token=0.01))
        assert caps.cost_model is not None
        assert caps.cost_model.input_cost_per_token == 0.01


class TestProviderInfoAndMetadata:
    def test_builtin_metadata(self) -> None:
        info = get_provider_info("claude_code")
        assert info is not None
        assert info.version == "1.0.0"

    def test_opencode_metadata(self) -> None:
        assert get_provider_info("opencode") is not None

    def test_unknown_returns_none(self) -> None:
        assert get_provider_info("nope") is None

    def test_register_with_metadata(self) -> None:
        class F:
            @property
            def name(self) -> str:
                return "f"

        meta = ProviderMetadata(name="f", version="0.5.0")
        register_provider("f", F, metadata=meta)
        assert get_provider_info("f") is meta
        del _REGISTRY["f"]
        del _METADATA["f"]

    def test_register_with_launch_command(self) -> None:
        class G:
            @property
            def name(self) -> str:
                return "g"

        register_provider("g", G, launch_command="g-agent")
        assert get_launch_command("g") == "g-agent"
        del _REGISTRY["g"]
        del _LAUNCH_COMMANDS["g"]


class TestPluginLoading:
    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        assert load_plugins(tmp_path / "nope") == []

    def test_valid_plugin(self, tmp_path: Path) -> None:
        code = textwrap.dedent("""
            from atc.agents.base import (
                ProviderCapabilities, ProviderMetadata,
                SessionInfo, SessionStatus,
            )
            PROVIDER_NAME = "tp"
            PROVIDER_METADATA = ProviderMetadata(name="tp", version="0.2.0")
            LAUNCH_COMMAND = "tp-cmd"
            class P:
                @property
                def name(self): return "tp"
                def get_capabilities(self): return ProviderCapabilities()
                async def spawn_session(self, sid, **kw):
                    return SessionInfo(session_id=sid, status=SessionStatus.IDLE)
                async def send_prompt(self, sid, p): pass
                async def get_status(self, sid): pass
                async def stream_output(self, sid): yield
                async def stop_session(self, sid): pass
                async def list_sessions(self): return []
            PROVIDER_CLASS = P
        """).strip()
        (tmp_path / "tp.py").write_text(code)
        loaded = load_plugins(tmp_path)
        assert "tp" in loaded
        info = get_provider_info("tp")
        assert info is not None
        assert info.version == "0.2.0"
        assert get_launch_command("tp") == "tp-cmd"
        del _REGISTRY["tp"]
        del _METADATA["tp"]
        del _LAUNCH_COMMANDS["tp"]
        _SCANNED_DIRS.discard(str(tmp_path.resolve()))

    def test_skip_underscore(self, tmp_path: Path) -> None:
        (tmp_path / "_x.py").write_text("PROVIDER_NAME='x'\nPROVIDER_CLASS=int")
        assert load_plugins(tmp_path) == []
        _SCANNED_DIRS.discard(str(tmp_path.resolve()))

    def test_skip_incomplete(self, tmp_path: Path) -> None:
        (tmp_path / "inc.py").write_text("x = 1")
        assert load_plugins(tmp_path) == []
        _SCANNED_DIRS.discard(str(tmp_path.resolve()))

    def test_skip_already_scanned(self, tmp_path: Path) -> None:
        (tmp_path / "d.py").write_text("PROVIDER_NAME='d2'\nclass D: pass\nPROVIDER_CLASS=D")
        assert "d2" in load_plugins(tmp_path)
        assert load_plugins(tmp_path) == []
        del _REGISTRY["d2"]
        _SCANNED_DIRS.discard(str(tmp_path.resolve()))

    def test_broken_plugin(self, tmp_path: Path) -> None:
        (tmp_path / "bad.py").write_text("raise RuntimeError('boom')")
        assert load_plugins(tmp_path) == []
        _SCANNED_DIRS.discard(str(tmp_path.resolve()))


class TestConfigPluginDirs:
    def test_default_empty(self) -> None:
        from atc.config import AgentProviderConfig

        assert AgentProviderConfig().plugin_dirs == []

    def test_configurable(self) -> None:
        from atc.config import AgentProviderConfig

        cfg = AgentProviderConfig(plugin_dirs=["/a", "/b"])
        assert len(cfg.plugin_dirs) == 2

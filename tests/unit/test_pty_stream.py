"""Unit tests for PtyStreamReader and PtyStreamPool."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — used at runtime by pytest fixtures
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atc.core.events import EventBus
from atc.terminal.pty_stream import PtyStreamPool, PtyStreamReader


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def tmp_fifo_dir(tmp_path: Path) -> Path:
    d = tmp_path / "fifos"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# PtyStreamReader
# ---------------------------------------------------------------------------

class TestPtyStreamReader:
    @pytest.fixture
    def reader(self, event_bus: EventBus, tmp_fifo_dir: Path) -> PtyStreamReader:
        return PtyStreamReader(
            session_id="sess-1",
            tmux_pane="%0",
            event_bus=event_bus,
            fifo_dir=tmp_fifo_dir,
        )

    def test_properties(self, reader: PtyStreamReader) -> None:
        assert reader.session_id == "sess-1"
        assert reader.fifo_path is None
        assert reader.running is False

    @patch.object(PtyStreamReader, "_run_tmux", new_callable=AsyncMock)
    async def test_start_creates_fifo(
        self, mock_tmux: AsyncMock, reader: PtyStreamReader, tmp_fifo_dir: Path
    ) -> None:
        mock_tmux.return_value = ""
        await reader.start()
        assert reader.running is True
        assert reader.fifo_path is not None
        assert reader.fifo_path.exists()
        # Verify pipe-pane was called
        mock_tmux.assert_called_once()
        args = mock_tmux.call_args[0]
        assert args[0] == "pipe-pane"
        await reader.stop()

    @patch.object(PtyStreamReader, "_run_tmux", new_callable=AsyncMock)
    async def test_start_idempotent(
        self, mock_tmux: AsyncMock, reader: PtyStreamReader
    ) -> None:
        mock_tmux.return_value = ""
        await reader.start()
        await reader.start()  # second call should be no-op
        assert mock_tmux.call_count == 1
        await reader.stop()

    @patch.object(PtyStreamReader, "_run_tmux", new_callable=AsyncMock)
    async def test_stop_cleans_up(
        self, mock_tmux: AsyncMock, reader: PtyStreamReader, tmp_fifo_dir: Path
    ) -> None:
        mock_tmux.return_value = ""
        await reader.start()
        fifo_path = reader.fifo_path
        assert fifo_path is not None

        await reader.stop()
        assert reader.running is False
        assert not fifo_path.exists()

    async def test_stop_when_not_started(self, reader: PtyStreamReader) -> None:
        """Stopping a reader that was never started should be a no-op."""
        await reader.stop()
        assert reader.running is False

    def test_on_data_registers_callback(self, reader: PtyStreamReader) -> None:
        cb = MagicMock()
        reader.on_data(cb)
        assert cb in reader._callbacks

    @patch.object(PtyStreamReader, "_run_tmux", new_callable=AsyncMock)
    async def test_dispatch_calls_callbacks(
        self, mock_tmux: AsyncMock, reader: PtyStreamReader, event_bus: EventBus
    ) -> None:
        mock_tmux.return_value = ""
        received: list[tuple[str, bytes]] = []

        def cb(sid: str, data: bytes) -> None:
            received.append((sid, data))

        reader.on_data(cb)

        # Directly test _dispatch without full start
        await reader._dispatch(b"hello")
        assert len(received) == 1
        assert received[0] == ("sess-1", b"hello")

    @patch.object(PtyStreamReader, "_run_tmux", new_callable=AsyncMock)
    async def test_dispatch_publishes_event(
        self, mock_tmux: AsyncMock, reader: PtyStreamReader, event_bus: EventBus
    ) -> None:
        mock_tmux.return_value = ""
        events: list[dict] = []
        event_bus.subscribe("pty_output", lambda data: events.append(data))

        await reader._dispatch(b"world")
        # Event handler is a coroutine in this test, so wrap it
        assert len(events) == 1
        assert events[0]["session_id"] == "sess-1"
        assert events[0]["data"] == b"world"

    @patch.object(PtyStreamReader, "_run_tmux", new_callable=AsyncMock)
    async def test_dispatch_async_callback(
        self, mock_tmux: AsyncMock, reader: PtyStreamReader
    ) -> None:
        received: list[bytes] = []

        async def async_cb(sid: str, data: bytes) -> None:
            received.append(data)

        reader.on_data(async_cb)
        await reader._dispatch(b"async-data")
        assert received == [b"async-data"]


# ---------------------------------------------------------------------------
# PtyStreamPool
# ---------------------------------------------------------------------------

class TestPtyStreamPool:
    @pytest.fixture
    def pool(self, event_bus: EventBus, tmp_fifo_dir: Path) -> PtyStreamPool:
        return PtyStreamPool(event_bus=event_bus, fifo_dir=tmp_fifo_dir)

    async def test_start_creates_dir(self, event_bus: EventBus, tmp_path: Path) -> None:
        new_dir = tmp_path / "new_fifos"
        pool = PtyStreamPool(event_bus=event_bus, fifo_dir=new_dir)
        await pool.start()
        assert new_dir.exists()
        await pool.stop()

    async def test_session_ids_initially_empty(self, pool: PtyStreamPool) -> None:
        assert pool.session_ids == []

    @patch.object(PtyStreamReader, "_run_tmux", new_callable=AsyncMock)
    async def test_add_session(self, mock_tmux: AsyncMock, pool: PtyStreamPool) -> None:
        mock_tmux.return_value = ""
        reader = await pool.add_session("sess-1", "%0")
        assert "sess-1" in pool.session_ids
        assert pool.get_reader("sess-1") is reader
        await pool.stop()

    @patch.object(PtyStreamReader, "_run_tmux", new_callable=AsyncMock)
    async def test_remove_session(self, mock_tmux: AsyncMock, pool: PtyStreamPool) -> None:
        mock_tmux.return_value = ""
        await pool.add_session("sess-1", "%0")
        await pool.remove_session("sess-1")
        assert "sess-1" not in pool.session_ids
        assert pool.get_reader("sess-1") is None

    @patch.object(PtyStreamReader, "_run_tmux", new_callable=AsyncMock)
    async def test_add_session_replaces_existing(
        self, mock_tmux: AsyncMock, pool: PtyStreamPool
    ) -> None:
        mock_tmux.return_value = ""
        reader1 = await pool.add_session("sess-1", "%0")
        reader2 = await pool.add_session("sess-1", "%1")
        assert pool.get_reader("sess-1") is reader2
        assert reader1.running is False
        await pool.stop()

    @patch.object(PtyStreamReader, "_run_tmux", new_callable=AsyncMock)
    async def test_stop_all(self, mock_tmux: AsyncMock, pool: PtyStreamPool) -> None:
        mock_tmux.return_value = ""
        await pool.add_session("sess-1", "%0")
        await pool.add_session("sess-2", "%1")
        await pool.stop()
        assert pool.session_ids == []

    def test_get_reader_nonexistent(self, pool: PtyStreamPool) -> None:
        assert pool.get_reader("nonexistent") is None

    @patch.object(PtyStreamReader, "_run_tmux", new_callable=AsyncMock)
    @patch("atc.terminal.pty_stream.send_keys_async", new_callable=AsyncMock)
    async def test_send_keys(
        self,
        mock_send_keys: AsyncMock,
        mock_tmux: AsyncMock,
        pool: PtyStreamPool,
    ) -> None:
        mock_tmux.return_value = ""
        await pool.add_session("sess-1", "%0")
        await pool.send_keys("sess-1", "ls -la")
        mock_send_keys.assert_called_once_with("atc", "%0", "ls -la")
        await pool.stop()

    async def test_send_keys_no_reader(self, pool: PtyStreamPool) -> None:
        with pytest.raises(ValueError, match="No active reader"):
            await pool.send_keys("nonexistent", "keys")

    @patch.object(PtyStreamReader, "_run_tmux", new_callable=AsyncMock)
    @patch("atc.terminal.pty_stream.send_instruction_async", new_callable=AsyncMock)
    async def test_send_instruction(
        self,
        mock_send_instr: AsyncMock,
        mock_tmux: AsyncMock,
        pool: PtyStreamPool,
    ) -> None:
        mock_tmux.return_value = ""
        await pool.add_session("sess-1", "%0")
        await pool.send_instruction("sess-1", "do something")
        mock_send_instr.assert_called_once_with("atc", "%0", "do something")
        await pool.stop()

    async def test_send_instruction_no_reader(self, pool: PtyStreamPool) -> None:
        with pytest.raises(ValueError, match="No active reader"):
            await pool.send_instruction("nonexistent", "text")

    @patch.object(PtyStreamReader, "_run_tmux", new_callable=AsyncMock)
    async def test_check_tui_ready(self, mock_tmux: AsyncMock, pool: PtyStreamPool) -> None:
        mock_tmux.return_value = "0"
        await pool.add_session("sess-1", "%0")
        # Reset mock so we can check just check_tui_ready calls
        mock_tmux.reset_mock()
        mock_tmux.return_value = "0"
        ready = await pool.check_tui_ready("sess-1")
        assert ready is True
        await pool.stop()

    @patch.object(PtyStreamReader, "_run_tmux", new_callable=AsyncMock)
    async def test_check_tui_not_ready(self, mock_tmux: AsyncMock, pool: PtyStreamPool) -> None:
        mock_tmux.return_value = ""
        await pool.add_session("sess-1", "%0")
        mock_tmux.reset_mock()
        mock_tmux.return_value = "1"
        ready = await pool.check_tui_ready("sess-1")
        assert ready is False
        await pool.stop()

    async def test_check_tui_ready_no_reader(self, pool: PtyStreamPool) -> None:
        with pytest.raises(ValueError, match="No active reader"):
            await pool.check_tui_ready("nonexistent")

    @patch.object(PtyStreamReader, "_run_tmux", new_callable=AsyncMock)
    async def test_capture_pane(self, mock_tmux: AsyncMock, pool: PtyStreamPool) -> None:
        mock_tmux.return_value = ""
        await pool.add_session("sess-1", "%0")
        mock_tmux.reset_mock()
        mock_tmux.return_value = "some terminal content"
        content = await pool.capture_pane("sess-1")
        assert content == "some terminal content"
        await pool.stop()

    async def test_capture_pane_no_reader(self, pool: PtyStreamPool) -> None:
        with pytest.raises(ValueError, match="No active reader"):
            await pool.capture_pane("nonexistent")

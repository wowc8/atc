"""PTY streaming via tmux pipe-pane → FIFO → asyncio reader.

PtyStreamReader  — reads from a per-session FIFO created by tmux pipe-pane.
PtyStreamPool    — manages readers for all active sessions, lifecycle hooks.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any


def _tmux_binary() -> str:
    """Resolve the tmux binary, checking common macOS/Linux install locations."""
    tmux = shutil.which("tmux")
    if tmux:
        return tmux
    for candidate in ("/usr/local/bin/tmux", "/opt/homebrew/bin/tmux", "/usr/bin/tmux"):
        if os.path.isfile(candidate):
            return candidate
    return "tmux"

from atc.terminal.control import send_instruction_async, send_keys_async

if TYPE_CHECKING:
    from atc.core.events import EventBus

logger = logging.getLogger(__name__)

# Default directory for FIFO files
FIFO_DIR = Path("/tmp/atc/fifos")

# Read buffer size (64 KiB)
READ_BUFFER = 65536


class PtyStreamReader:
    """Reads raw PTY output from a per-session FIFO.

    tmux pipe-pane writes terminal output into a named pipe (FIFO).
    This reader opens the FIFO in non-blocking mode and publishes
    binary chunks to registered callbacks and the WebSocket hub.

    Lifecycle:
        1. ``start()`` — create FIFO, attach tmux pipe-pane, begin reading.
        2. Reading loop runs until ``stop()`` is called.
        3. ``stop()`` — detach pipe-pane, close FIFO, clean up.
    """

    def __init__(
        self,
        session_id: str,
        tmux_pane: str,
        event_bus: EventBus,
        fifo_dir: Path = FIFO_DIR,
    ) -> None:
        self._session_id = session_id
        self._tmux_pane = tmux_pane
        self._event_bus = event_bus
        self._fifo_dir = fifo_dir
        self._fifo_path: Path | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._callbacks: list[Any] = []

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def fifo_path(self) -> Path | None:
        return self._fifo_path

    @property
    def running(self) -> bool:
        return self._running

    def on_data(self, callback: Any) -> None:
        """Register a callback invoked with (session_id, bytes) on each chunk."""
        self._callbacks.append(callback)

    async def start(self) -> None:
        """Create FIFO, attach tmux pipe-pane, and begin the read loop."""
        if self._running:
            return

        self._fifo_dir.mkdir(parents=True, exist_ok=True)
        fifo = self._fifo_dir / f"{self._session_id}.fifo"

        # Create FIFO if it doesn't exist
        if fifo.exists():
            fifo.unlink()
        os.mkfifo(str(fifo))
        self._fifo_path = fifo

        # Attach tmux pipe-pane to write output into the FIFO
        await self._run_tmux(
            "pipe-pane", "-t", self._tmux_pane, f"cat >> {fifo}"
        )

        self._running = True
        self._task = asyncio.create_task(self._read_loop(), name=f"pty-{self._session_id}")
        logger.info(
            "PtyStreamReader started for session %s (pane %s)",
            self._session_id, self._tmux_pane,
        )

    async def stop(self) -> None:
        """Detach pipe-pane, cancel reader task, and clean up FIFO."""
        if not self._running:
            return

        self._running = False

        # Detach tmux pipe-pane (empty string disables piping)
        try:
            await self._run_tmux("pipe-pane", "-t", self._tmux_pane, "")
        except Exception:
            logger.warning("Failed to detach pipe-pane for %s", self._session_id)

        # Cancel the read loop
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        # Remove FIFO
        if self._fifo_path and self._fifo_path.exists():
            with contextlib.suppress(OSError):
                self._fifo_path.unlink()

        logger.info("PtyStreamReader stopped for session %s", self._session_id)

    async def _read_loop(self) -> None:
        """Continuously read from FIFO and dispatch chunks."""
        while self._running:
            fd: int | None = None
            try:
                # Open FIFO non-blocking — blocks in asyncio until a writer attaches
                fd = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: os.open(str(self._fifo_path), os.O_RDONLY | os.O_NONBLOCK)
                )

                stream_reader = asyncio.StreamReader()
                protocol = asyncio.StreamReaderProtocol(stream_reader)
                read_transport, _ = await asyncio.get_event_loop().connect_read_pipe(
                    lambda _proto=protocol: _proto,
                    os.fdopen(fd, "rb", closefd=False),
                )

                try:
                    while self._running:
                        chunk = await stream_reader.read(READ_BUFFER)
                        if not chunk:
                            # Writer closed — reopen
                            break
                        await self._dispatch(chunk)
                finally:
                    read_transport.close()

            except asyncio.CancelledError:
                raise
            except Exception:
                if self._running:
                    logger.exception("Read error for session %s", self._session_id)
                    await asyncio.sleep(0.5)
            finally:
                if fd is not None:
                    with contextlib.suppress(OSError):
                        os.close(fd)

    async def _dispatch(self, chunk: bytes) -> None:
        """Forward a raw PTY chunk to callbacks and the event bus."""
        for cb in self._callbacks:
            try:
                result = cb(self._session_id, chunk)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("Callback error for session %s", self._session_id)

        # Publish on the event bus for WsHub integration
        await self._event_bus.publish("pty_output", {
            "session_id": self._session_id,
            "data": chunk,
        })

    @staticmethod
    async def _run_tmux(*args: str) -> str:
        """Execute a tmux command and return stdout."""
        proc = await asyncio.create_subprocess_exec(
            _tmux_binary(), *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            msg = stderr.decode(errors="replace").strip()
            raise RuntimeError(f"tmux {args[0]} failed: {msg}")
        return stdout.decode(errors="replace").strip()


class PtyStreamPool:
    """Manages PtyStreamReader instances for all active sessions.

    Provides lifecycle management (start/stop readers), lookup by session ID,
    and integration with the WsHub for binary frame distribution.
    """

    def __init__(
        self,
        event_bus: EventBus,
        fifo_dir: Path = FIFO_DIR,
        tmux_session: str = "atc",
    ) -> None:
        self._event_bus = event_bus
        self._fifo_dir = fifo_dir
        self._tmux_session = tmux_session
        self._readers: dict[str, PtyStreamReader] = {}
        self._started = False

    @property
    def session_ids(self) -> list[str]:
        """List of session IDs with active readers."""
        return list(self._readers.keys())

    def get_reader(self, session_id: str) -> PtyStreamReader | None:
        """Return the reader for *session_id*, or ``None``."""
        return self._readers.get(session_id)

    async def start(self) -> None:
        """Start the pool (ensure FIFO directory exists)."""
        self._fifo_dir.mkdir(parents=True, exist_ok=True)
        self._started = True
        logger.info("PtyStreamPool started (fifo_dir=%s)", self._fifo_dir)

    async def stop(self) -> None:
        """Stop all readers and shut down the pool."""
        for reader in list(self._readers.values()):
            await reader.stop()
        self._readers.clear()
        self._started = False
        logger.info("PtyStreamPool stopped")

    async def add_session(
        self,
        session_id: str,
        tmux_pane: str,
    ) -> PtyStreamReader:
        """Create and start a reader for the given session.

        If a reader already exists for *session_id*, it is stopped first.
        """
        if session_id in self._readers:
            await self.remove_session(session_id)

        reader = PtyStreamReader(
            session_id=session_id,
            tmux_pane=tmux_pane,
            event_bus=self._event_bus,
            fifo_dir=self._fifo_dir,
        )
        await reader.start()
        self._readers[session_id] = reader
        return reader

    async def remove_session(self, session_id: str) -> None:
        """Stop and remove the reader for *session_id*."""
        reader = self._readers.pop(session_id, None)
        if reader:
            await reader.stop()

    async def resize_pane(self, session_id: str, cols: int, rows: int) -> None:
        """Resize a session's tmux pane to the given dimensions.

        Called when the frontend xterm.js terminal is resized so the PTY
        output matches the actual display width.
        """
        reader = self._readers.get(session_id)
        if reader is None:
            raise ValueError(f"No active reader for session {session_id}")
        await PtyStreamReader._run_tmux(
            "resize-window", "-t", reader._tmux_pane, "-x", str(cols), "-y", str(rows),
        )

    async def send_keys(self, session_id: str, keys: str) -> None:
        """Send keystrokes to a session's tmux pane via hex-encoded send-keys.

        This is the input path: keystrokes from the frontend WebSocket
        are forwarded to the tmux pane via the control module.
        """
        reader = self._readers.get(session_id)
        if reader is None:
            raise ValueError(f"No active reader for session {session_id}")
        await send_keys_async(self._tmux_session, reader._tmux_pane, keys)

    async def send_instruction(self, session_id: str, text: str) -> None:
        """Send an instruction as bracketed paste followed by Enter.

        Uses the control module for reliable hex-encoded delivery with
        bracketed paste wrapping and auto-retry on connection failure.
        """
        reader = self._readers.get(session_id)
        if reader is None:
            raise ValueError(f"No active reader for session {session_id}")
        await send_instruction_async(self._tmux_session, reader._tmux_pane, text)

    async def check_tui_ready(self, session_id: str) -> bool:
        """Check if the session's TUI is ready for input (not in alternate screen).

        Returns True if the pane is NOT in alternate screen mode.
        """
        reader = self._readers.get(session_id)
        if reader is None:
            raise ValueError(f"No active reader for session {session_id}")

        try:
            output = await PtyStreamReader._run_tmux(
                "display-message", "-t", reader._tmux_pane, "-p", "#{alternate_on}"
            )
            return output.strip() == "0"
        except RuntimeError:
            return False

    async def capture_pane(self, session_id: str) -> str:
        """Capture the current visible content of a session's tmux pane.

        Uses ``-e`` to preserve ANSI escape sequences (colors) and ``-J``
        to join wrapped lines so xterm.js can re-wrap them at its actual
        display width instead of inheriting the tmux pane width.
        """
        reader = self._readers.get(session_id)
        if reader is None:
            raise ValueError(f"No active reader for session {session_id}")

        return await PtyStreamReader._run_tmux(
            "capture-pane", "-t", reader._tmux_pane, "-p", "-e", "-J"
        )

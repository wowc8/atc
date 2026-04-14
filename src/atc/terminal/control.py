"""tmux control mode + hex send-keys + bracketed paste for reliable delivery.

TmuxControlConnection  — wraps a persistent ``tmux -C attach-session`` subprocess.
TmuxControlPool        — singleton pool, one connection per tmux session.

Top-level helpers
-----------------
send_keys_async        — hex-encode and send keystrokes (retries once on failure)
send_instruction_async — bracketed-paste instruction + Enter (retries once)
capture_pane_async     — subprocess capture-pane (control mode can't do this)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from pathlib import Path
from typing import ClassVar

logger = logging.getLogger(__name__)


def _tmux_binary() -> str:
    """Resolve tmux from PATH or common install locations."""
    tmux = shutil.which("tmux")
    if tmux:
        return tmux
    for candidate in ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux", "/usr/bin/tmux"):
        if shutil.which(candidate) or Path(candidate).exists():
            return candidate
    return "tmux"

# Bracketed paste escape sequences
# ESC [ 2 0 0 ~  →  start of paste
_BP_PREFIX = bytes([0x1B, 0x5B, 0x32, 0x30, 0x30, 0x7E])
# ESC [ 2 0 1 ~  →  end of paste
_BP_SUFFIX = bytes([0x1B, 0x5B, 0x32, 0x30, 0x31, 0x7E])

# Enter key name for tmux send-keys.
#
# On macOS tmux control mode, `send-keys -H ... 0d` can leave pasted input sitting
# at the prompt without actually submitting it. A symbolic `Enter` key works
# reliably there and on Linux.
_ENTER_KEY = "Enter"

# Small settle delay between bracketed paste and Enter.
# Without this, tmux control mode on macOS can leave the pasted instruction
# sitting visibly at the Claude prompt even though both commands were written.
_ENTER_SETTLE_DELAY = 0.2


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------


def _to_hex(data: bytes) -> str:
    """Encode *data* as a space-separated lowercase hex string.

    >>> _to_hex(b"hi")
    '68 69'
    """
    return " ".join(f"{b:02x}" for b in data)


def _encode_text(text: str, *, bracketed: bool = False) -> str:
    """Encode *text* as space-separated hex, optionally with bracketed paste wrappers.

    Args:
        text: UTF-8 text to encode.
        bracketed: when True, prepend ESC[200~ and append ESC[201~ before encoding.
    """
    raw = text.encode("utf-8")
    if bracketed:
        raw = _BP_PREFIX + raw + _BP_SUFFIX
    return _to_hex(raw)


# ---------------------------------------------------------------------------
# TmuxControlConnection
# ---------------------------------------------------------------------------


class TmuxControlConnection:
    """Wraps a persistent ``tmux -C attach-session -t SESSION`` subprocess.

    Control mode accepts commands written to stdin and streams notifications on
    stdout.  The stdout *must* be continuously drained — if the pipe buffer fills
    the stdin write will block indefinitely.

    Lifecycle::

        conn = TmuxControlConnection("atc")
        ok = await conn.start()   # False if session missing
        await conn.send_keys("%42", "hello world", bracketed=True)
        await conn.send_enter("%42")
        await conn.stop()
    """

    def __init__(self, tmux_session: str) -> None:
        self._tmux_session = tmux_session
        self._proc: asyncio.subprocess.Process | None = None
        self._drain_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_alive(self) -> bool:
        """True when the subprocess is running (returncode is None)."""
        return self._proc is not None and self._proc.returncode is None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> bool:
        """Start the tmux control-mode subprocess.

        Returns:
            ``True`` on success, ``False`` if the tmux session doesn't exist or
            the subprocess exits immediately.
        """
        if self.is_alive:
            return True

        try:
            self._proc = await asyncio.create_subprocess_exec(
                _tmux_binary(),
                "-C",
                "attach-session",
                "-t",
                self._tmux_session,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            logger.warning(
                "TmuxControlConnection: failed to start for session %s: %s",
                self._tmux_session,
                exc,
            )
            return False

        # Give tmux a moment to reject a missing session
        await asyncio.sleep(0.05)
        if self._proc.returncode is not None:
            logger.warning(
                "TmuxControlConnection: tmux exited immediately for session %s "
                "(returncode %d)",
                self._tmux_session,
                self._proc.returncode,
            )
            self._proc = None
            return False

        # Start draining stdout so the pipe never fills
        self._drain_task = asyncio.create_task(
            self._drain_stdout(),
            name=f"tmux-drain-{self._tmux_session}",
        )
        logger.info(
            "TmuxControlConnection: started for session %s", self._tmux_session
        )
        return True

    async def stop(self) -> None:
        """Stop the drain task and terminate the subprocess."""
        if self._drain_task and not self._drain_task.done():
            self._drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._drain_task
        self._drain_task = None

        if self._proc is not None and self._proc.returncode is None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                await self._proc.wait()
            except Exception:
                with contextlib.suppress(Exception):
                    self._proc.kill()
                    await self._proc.wait()

        self._proc = None
        logger.info(
            "TmuxControlConnection: stopped for session %s", self._tmux_session
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _drain_stdout(self) -> None:
        """Continuously drain stdout to prevent the pipe buffer from filling."""
        if self._proc is None or self._proc.stdout is None:
            return
        try:
            while True:
                chunk = await self._proc.stdout.read(4096)
                if not chunk:
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug(
                "TmuxControlConnection: stdout drain ended for %s", self._tmux_session
            )

    # ------------------------------------------------------------------
    # Send helpers
    # ------------------------------------------------------------------

    async def send_keys(
        self, target: str, text: str, *, bracketed: bool = False
    ) -> None:
        """Send *text* to *target* via hex-encoded send-keys.

        Args:
            target: tmux target spec (e.g. pane_id ``%42`` or ``session:win.pane``).
            text: text to transmit.
            bracketed: when True, wraps the payload in ESC[200~/ESC[201~ so
                readline treats it as a paste rather than typed input.

        Raises:
            RuntimeError: if the connection is not alive.
        """
        if not self.is_alive or self._proc is None or self._proc.stdin is None:
            raise RuntimeError(
                f"Control connection for {self._tmux_session!r} is not alive"
            )
        hex_str = _encode_text(text, bracketed=bracketed)
        cmd = f"send-keys -H -t {target} {hex_str}\n"
        self._proc.stdin.write(cmd.encode())
        await self._proc.stdin.drain()

    async def send_enter(self, target: str) -> None:
        """Send the Enter key to *target*.

        Uses tmux's symbolic `Enter` key instead of a hex carriage return.
        Control mode on macOS accepted the pasted text but sometimes ignored a
        hex `0d`, leaving the instruction visibly pending at the prompt.

        Raises:
            RuntimeError: if the connection is not alive.
        """
        if not self.is_alive or self._proc is None or self._proc.stdin is None:
            raise RuntimeError(
                f"Control connection for {self._tmux_session!r} is not alive"
            )
        cmd = f"send-keys -t {target} {_ENTER_KEY}\n"
        self._proc.stdin.write(cmd.encode())
        await self._proc.stdin.drain()


# ---------------------------------------------------------------------------
# TmuxControlPool
# ---------------------------------------------------------------------------


class TmuxControlPool:
    """Singleton pool of :class:`TmuxControlConnection` instances.

    One persistent control-mode connection is kept per tmux session name.
    Dead connections are replaced automatically on the next
    :meth:`get_connection` call.

    Usage::

        pool = TmuxControlPool.get_instance()
        conn = await pool.get_connection("atc")
        await conn.send_keys("%42", "hello")
    """

    _instance: ClassVar[TmuxControlPool | None] = None

    def __init__(self) -> None:
        self._connections: dict[str, TmuxControlConnection] = {}

    @classmethod
    def get_instance(cls) -> TmuxControlPool:
        """Return the process-wide singleton, creating it on first call."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def get_connection(self, tmux_session: str) -> TmuxControlConnection:
        """Return an alive connection for *tmux_session*.

        If no connection exists or the existing one is dead, a fresh one is
        started and stored before returning.

        Raises:
            RuntimeError: if the tmux session cannot be attached to.
        """
        conn = self._connections.get(tmux_session)
        if conn is None or not conn.is_alive:
            if conn is not None:
                await conn.stop()
            conn = TmuxControlConnection(tmux_session)
            started = await conn.start()
            if not started:
                raise RuntimeError(
                    f"Failed to attach to tmux session {tmux_session!r}"
                )
            self._connections[tmux_session] = conn
        return conn

    async def close_all(self) -> None:
        """Stop all managed connections and clear the pool."""
        for conn in list(self._connections.values()):
            await conn.stop()
        self._connections.clear()
        logger.info("TmuxControlPool: all connections closed")


# ---------------------------------------------------------------------------
# Public async helpers
# ---------------------------------------------------------------------------


async def send_keys_async(
    tmux_session: str,
    target: str,
    text: str,
    *,
    bracketed: bool = False,
) -> None:
    """Send keystrokes to *target* via the control pool.

    Retries once with a fresh connection if the first attempt fails.

    Args:
        tmux_session: tmux session name (used as pool key for the persistent
            control-mode connection).
        target: tmux target spec for the destination pane.
        text: text to transmit.
        bracketed: when True, wraps payload in bracketed paste sequences.
    """
    pool = TmuxControlPool.get_instance()
    try:
        conn = await pool.get_connection(tmux_session)
        await conn.send_keys(target, text, bracketed=bracketed)
    except Exception as exc:
        logger.warning(
            "send_keys_async: first attempt failed (%s), retrying with fresh connection",
            exc,
        )
        old = pool._connections.pop(tmux_session, None)
        if old is not None:
            await old.stop()
        conn = await pool.get_connection(tmux_session)
        await conn.send_keys(target, text, bracketed=bracketed)


async def send_instruction_async(
    tmux_session: str,
    target: str,
    text: str,
) -> None:
    """Send an instruction as bracketed paste followed by Enter.

    Retries once with a fresh connection if the first attempt fails.

    Args:
        tmux_session: tmux session name (pool key).
        target: tmux target spec for the destination pane.
        text: instruction text to deliver.
    """
    pool = TmuxControlPool.get_instance()
    try:
        conn = await pool.get_connection(tmux_session)
        await conn.send_keys(target, text, bracketed=True)
        await asyncio.sleep(_ENTER_SETTLE_DELAY)
        await conn.send_enter(target)
    except Exception as exc:
        logger.warning(
            "send_instruction_async: first attempt failed (%s), retrying with fresh connection",
            exc,
        )
        old = pool._connections.pop(tmux_session, None)
        if old is not None:
            await old.stop()
        conn = await pool.get_connection(tmux_session)
        await conn.send_keys(target, text, bracketed=True)
        await asyncio.sleep(_ENTER_SETTLE_DELAY)
        await conn.send_enter(target)


async def capture_pane_async(tmux_session: str, target: str) -> str:  # noqa: ARG001
    """Capture the current visible content of a tmux pane.

    Uses a regular subprocess — capture-pane is not available in control mode.

    Args:
        tmux_session: tmux session name (unused; kept for API symmetry).
        target: tmux target spec for the pane to capture.

    Returns:
        Captured pane content (ANSI sequences preserved, lines joined).

    Raises:
        RuntimeError: if ``tmux capture-pane`` exits non-zero.
    """
    proc = await asyncio.create_subprocess_exec(
        _tmux_binary(),
        "capture-pane",
        "-t",
        target,
        "-p",
        "-e",
        "-J",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = stderr.decode(errors="replace").strip()
        raise RuntimeError(f"tmux capture-pane failed: {msg}")
    return stdout.decode(errors="replace")

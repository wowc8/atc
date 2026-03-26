"""Unit tests for atc.terminal.control.

Covers:
- Hex encoding logic
- Bracketed paste byte wrapping
- TmuxControlPool singleton behaviour
- Retry logic (mock dead connection, verify retry with fresh one)
- Graceful handling when tmux session doesn't exist
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atc.terminal.control import (
    TmuxControlConnection,
    TmuxControlPool,
    _BP_PREFIX,
    _BP_SUFFIX,
    _ENTER_HEX,
    _encode_text,
    _to_hex,
    capture_pane_async,
    send_instruction_async,
    send_keys_async,
)


# ---------------------------------------------------------------------------
# Hex encoding
# ---------------------------------------------------------------------------


def test_to_hex_basic() -> None:
    assert _to_hex(b"hello") == "68 65 6c 6c 6f"


def test_to_hex_single_byte() -> None:
    assert _to_hex(b"\x00") == "00"


def test_to_hex_empty() -> None:
    assert _to_hex(b"") == ""


def test_encode_text_simple() -> None:
    assert _encode_text("hi") == "68 69"


def test_encode_text_special_chars_round_trip() -> None:
    """Backtick, dollar, quote — hex encoding avoids all shell escaping."""
    text = '`echo $HOME` "quoted" \'single\''
    result = _encode_text(text)
    parts = result.split(" ")
    recovered = bytes(int(x, 16) for x in parts).decode("utf-8")
    assert recovered == text


def test_encode_text_unicode_round_trip() -> None:
    text = "こんにちは"
    result = _encode_text(text)
    parts = result.split(" ")
    recovered = bytes(int(x, 16) for x in parts).decode("utf-8")
    assert recovered == text


# ---------------------------------------------------------------------------
# Bracketed paste wrapping
# ---------------------------------------------------------------------------


def test_encode_text_bracketed_prefix_suffix() -> None:
    result = _encode_text("hi", bracketed=True)
    parts = result.split(" ")
    prefix_hex = [f"{b:02x}" for b in _BP_PREFIX]
    suffix_hex = [f"{b:02x}" for b in _BP_SUFFIX]

    # First 6 bytes == ESC[200~
    assert parts[: len(prefix_hex)] == prefix_hex
    # Last 6 bytes == ESC[201~
    assert parts[-len(suffix_hex) :] == suffix_hex
    # Middle bytes == "hi"
    middle = parts[len(prefix_hex) : -len(suffix_hex)]
    assert middle == ["68", "69"]


def test_encode_text_bracketed_round_trip() -> None:
    text = "Do something complex with $VARS and `backticks`"
    result = _encode_text(text, bracketed=True)
    parts = result.split(" ")
    raw = bytes(int(x, 16) for x in parts)
    assert raw == _BP_PREFIX + text.encode("utf-8") + _BP_SUFFIX


def test_enter_hex_is_carriage_return() -> None:
    assert _ENTER_HEX == "0d"
    assert bytes([int(_ENTER_HEX, 16)]) == b"\r"


# ---------------------------------------------------------------------------
# TmuxControlPool singleton
# ---------------------------------------------------------------------------


def test_pool_singleton() -> None:
    TmuxControlPool._instance = None
    p1 = TmuxControlPool.get_instance()
    p2 = TmuxControlPool.get_instance()
    assert p1 is p2


def test_pool_singleton_same_after_multiple_calls() -> None:
    TmuxControlPool._instance = None
    instances = [TmuxControlPool.get_instance() for _ in range(5)]
    assert all(i is instances[0] for i in instances)


@pytest.mark.asyncio
async def test_pool_close_all_stops_connections() -> None:
    TmuxControlPool._instance = None
    pool = TmuxControlPool.get_instance()

    fake_conn = MagicMock(spec=TmuxControlConnection)
    fake_conn.stop = AsyncMock()
    pool._connections["my-session"] = fake_conn

    await pool.close_all()

    fake_conn.stop.assert_called_once()
    assert pool._connections == {}


@pytest.mark.asyncio
async def test_pool_get_connection_replaces_dead() -> None:
    TmuxControlPool._instance = None
    pool = TmuxControlPool.get_instance()

    dead = MagicMock(spec=TmuxControlConnection)
    dead.is_alive = False
    dead.stop = AsyncMock()
    pool._connections["atc"] = dead

    fresh = MagicMock(spec=TmuxControlConnection)
    fresh.is_alive = True
    fresh.start = AsyncMock(return_value=True)

    with patch("atc.terminal.control.TmuxControlConnection", return_value=fresh):
        conn = await pool.get_connection("atc")

    dead.stop.assert_called_once()
    assert conn is fresh
    assert pool._connections["atc"] is fresh


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_keys_async_retries_on_failure() -> None:
    TmuxControlPool._instance = None
    pool = TmuxControlPool.get_instance()

    fresh = MagicMock(spec=TmuxControlConnection)
    fresh.is_alive = True
    fresh.stop = AsyncMock()
    fresh.send_keys = AsyncMock()

    call_count = 0

    async def fake_get(session: str) -> TmuxControlConnection:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            failing = MagicMock(spec=TmuxControlConnection)
            failing.is_alive = True
            failing.stop = AsyncMock()
            failing.send_keys = AsyncMock(side_effect=RuntimeError("pipe broken"))
            return failing
        return fresh

    with patch.object(pool, "get_connection", side_effect=fake_get):
        await send_keys_async("atc", "%42", "hello")

    assert call_count == 2
    fresh.send_keys.assert_called_once_with("%42", "hello", bracketed=False)


@pytest.mark.asyncio
async def test_send_keys_async_bracketed_forwarded() -> None:
    TmuxControlPool._instance = None
    pool = TmuxControlPool.get_instance()

    conn = MagicMock(spec=TmuxControlConnection)
    conn.is_alive = True
    conn.send_keys = AsyncMock()

    with patch.object(pool, "get_connection", AsyncMock(return_value=conn)):
        await send_keys_async("atc", "%42", "text", bracketed=True)

    conn.send_keys.assert_called_once_with("%42", "text", bracketed=True)


@pytest.mark.asyncio
async def test_send_instruction_async_retries_on_failure() -> None:
    TmuxControlPool._instance = None
    pool = TmuxControlPool.get_instance()

    fresh = MagicMock(spec=TmuxControlConnection)
    fresh.is_alive = True
    fresh.stop = AsyncMock()
    fresh.send_keys = AsyncMock()
    fresh.send_enter = AsyncMock()

    call_count = 0

    async def fake_get(session: str) -> TmuxControlConnection:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            failing = MagicMock(spec=TmuxControlConnection)
            failing.is_alive = True
            failing.stop = AsyncMock()
            failing.send_keys = AsyncMock(side_effect=RuntimeError("pipe broken"))
            failing.send_enter = AsyncMock()
            return failing
        return fresh

    with patch.object(pool, "get_connection", side_effect=fake_get):
        await send_instruction_async("atc", "%42", "do something")

    assert call_count == 2
    fresh.send_keys.assert_called_once_with("%42", "do something", bracketed=True)
    fresh.send_enter.assert_called_once_with("%42")


@pytest.mark.asyncio
async def test_send_instruction_async_success_path() -> None:
    TmuxControlPool._instance = None
    pool = TmuxControlPool.get_instance()

    conn = MagicMock(spec=TmuxControlConnection)
    conn.is_alive = True
    conn.send_keys = AsyncMock()
    conn.send_enter = AsyncMock()

    with patch.object(pool, "get_connection", AsyncMock(return_value=conn)):
        await send_instruction_async("atc", "%7", "run the tests")

    conn.send_keys.assert_called_once_with("%7", "run the tests", bracketed=True)
    conn.send_enter.assert_called_once_with("%7")


# ---------------------------------------------------------------------------
# Graceful failure when tmux session doesn't exist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_control_connection_returns_false_when_session_missing() -> None:
    """If tmux exits immediately (bad session), start() returns False."""
    mock_proc = MagicMock()
    mock_proc.returncode = 1  # already exited

    async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
        return mock_proc

    conn = TmuxControlConnection("nonexistent-xyz-abc")
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = await conn.start()

    assert result is False
    assert not conn.is_alive


@pytest.mark.asyncio
async def test_control_connection_returns_false_on_os_error() -> None:
    """If tmux binary is missing, start() returns False."""

    async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
        raise OSError("No such file")

    conn = TmuxControlConnection("atc")
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = await conn.start()

    assert result is False
    assert not conn.is_alive


@pytest.mark.asyncio
async def test_pool_get_connection_raises_when_start_fails() -> None:
    TmuxControlPool._instance = None
    pool = TmuxControlPool.get_instance()

    bad_conn = MagicMock(spec=TmuxControlConnection)
    bad_conn.is_alive = False
    bad_conn.start = AsyncMock(return_value=False)
    bad_conn.stop = AsyncMock()

    with (
        patch("atc.terminal.control.TmuxControlConnection", return_value=bad_conn),
        pytest.raises(RuntimeError, match="Failed to attach"),
    ):
        await pool.get_connection("missing-session")


# ---------------------------------------------------------------------------
# capture_pane_async
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capture_pane_async_success() -> None:
    expected = "some terminal content\n$ "
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(expected.encode(), b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await capture_pane_async("atc", "%1")

    assert result == expected


@pytest.mark.asyncio
async def test_capture_pane_async_raises_on_failure() -> None:
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"no session"))

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        pytest.raises(RuntimeError, match="capture-pane failed"),
    ):
        await capture_pane_async("atc", "%99")


# ---------------------------------------------------------------------------
# TmuxControlConnection send helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_keys_writes_hex_command() -> None:
    """send_keys writes 'send-keys -H -t TARGET HEX\\n' to stdin."""
    mock_stdin = MagicMock()
    mock_stdin.drain = AsyncMock()

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.stdin = mock_stdin

    conn = TmuxControlConnection("atc")
    conn._proc = mock_proc

    await conn.send_keys("%5", "hi")

    written: bytes = mock_stdin.write.call_args[0][0]
    assert written.startswith(b"send-keys -H -t %5 ")
    # "hi" encodes to "68 69"
    assert b"68 69" in written
    mock_stdin.drain.assert_called_once()


@pytest.mark.asyncio
async def test_send_enter_writes_0d() -> None:
    mock_stdin = MagicMock()
    mock_stdin.drain = AsyncMock()

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.stdin = mock_stdin

    conn = TmuxControlConnection("atc")
    conn._proc = mock_proc

    await conn.send_enter("%5")

    written: bytes = mock_stdin.write.call_args[0][0]
    assert b"0d" in written
    mock_stdin.drain.assert_called_once()


@pytest.mark.asyncio
async def test_send_keys_raises_when_not_alive() -> None:
    conn = TmuxControlConnection("atc")
    # _proc is None → not alive
    with pytest.raises(RuntimeError, match="not alive"):
        await conn.send_keys("%1", "text")


@pytest.mark.asyncio
async def test_send_enter_raises_when_not_alive() -> None:
    conn = TmuxControlConnection("atc")
    with pytest.raises(RuntimeError, match="not alive"):
        await conn.send_enter("%1")

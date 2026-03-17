"""Integration tests for WebSocket hub.

Tests the WsHub in isolation and through the FastAPI WebSocket endpoint,
verifying channel subscriptions, broadcasts, and terminal input forwarding.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from atc.api.app import create_app
from atc.api.ws.hub import WsHub
from atc.config import Settings

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# WsHub unit-level integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def hub() -> WsHub:
    return WsHub()


class TestWsHubUnit:
    def test_initial_state(self, hub: WsHub) -> None:
        assert hub.client_count == 0

    @pytest.mark.asyncio
    async def test_broadcast_no_clients(self, hub: WsHub) -> None:
        """Broadcasting with no subscribers should not raise."""
        await hub.broadcast("terminal:abc", "hello")

    def test_on_input_registers_callback(self, hub: WsHub) -> None:
        cb = AsyncMock()
        hub.on_input(cb)
        assert hub._input_callback is cb


# ---------------------------------------------------------------------------
# WebSocket endpoint integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db_path = str(tmp_path / "test.db")
    settings = Settings(database={"path": db_path})  # type: ignore[arg-type]
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


class TestWebSocketEndpoint:
    def test_connect(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as ws:
            # Connection should succeed
            ws.send_json({"channel": "subscribe", "data": ["state"]})

    def test_subscribe_and_receive_broadcast(self, client: TestClient) -> None:
        """Subscribe to a channel and verify broadcasts are received."""
        app = client.app
        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {"channel": "subscribe", "data": ["terminal:test-123"]}
            )
            # Give the server a moment to process the subscribe
            # Then broadcast from the hub directly
            hub: WsHub = app.state.ws_hub  # type: ignore[union-attr]

            # We need to broadcast from an async context; use the event loop
            import threading

            broadcast_done = threading.Event()

            async def do_broadcast() -> None:
                await hub.broadcast("terminal:test-123", "hello from hub")
                broadcast_done.set()

            # The TestClient runs the app in a thread with its own event loop
            # We need to schedule the broadcast on that loop
            loop = asyncio.new_event_loop()
            t = threading.Thread(target=lambda: loop.run_until_complete(do_broadcast()))
            t.start()
            t.join(timeout=5)

            msg = ws.receive_json()
            assert msg["channel"] == "terminal:test-123"
            assert msg["data"] == "hello from hub"

    def test_malformed_message_ignored(self, client: TestClient) -> None:
        """Sending malformed JSON should not crash the connection."""
        with client.websocket_connect("/ws") as ws:
            ws.send_text("not valid json {{{")
            # Connection should still be alive
            ws.send_json({"channel": "subscribe", "data": ["state"]})

    def test_multiple_channel_subscribe(self, client: TestClient) -> None:
        """Client can subscribe to multiple channels at once."""
        with client.websocket_connect("/ws") as ws:
            ws.send_json(
                {
                    "channel": "subscribe",
                    "data": ["state", "terminal:s1", "terminal:s2"],
                }
            )

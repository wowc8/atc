"""WebSocket hub — channel-based pub/sub for frontend clients.

Clients connect via ``/ws``, send a subscribe message to join channels,
and receive JSON frames whenever data is published to those channels.

Protocol:
    Client → Server (subscribe):
        {"channel": "subscribe", "data": ["terminal:abc-123", "state"]}
    Server → Client (data frame):
        {"channel": "terminal:abc-123", "data": "<raw PTY output>"}
    Client → Server (terminal input):
        {"channel": "terminal:abc-123", "data": "ls\\n"}
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class WsHub:
    """Channel-based WebSocket pub/sub hub.

    Each connected client maintains a set of subscribed channels.
    ``broadcast`` sends a message to every client subscribed to a channel.
    """

    def __init__(self) -> None:
        self._clients: dict[WebSocket, set[str]] = {}
        self._input_callback: Any | None = None

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def on_input(self, callback: Any) -> None:
        """Register a callback for terminal input from clients.

        Callback signature: ``async def cb(channel: str, data: str) -> None``
        """
        self._input_callback = callback

    async def connect(self, ws: WebSocket) -> None:
        """Accept a new WebSocket connection."""
        await ws.accept()
        self._clients[ws] = set()
        logger.debug("WsHub: client connected (%d total)", len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        """Remove a disconnected client."""
        self._clients.pop(ws, None)
        logger.debug("WsHub: client disconnected (%d total)", len(self._clients))

    def subscribe(self, ws: WebSocket, channels: list[str]) -> None:
        """Subscribe a client to one or more channels."""
        if ws in self._clients:
            self._clients[ws].update(channels)

    async def broadcast(self, channel: str, data: Any) -> None:
        """Send a message to all clients subscribed to *channel*."""
        frame = json.dumps({"channel": channel, "data": data})
        dead: list[WebSocket] = []

        for ws, channels in self._clients.items():
            if channel in channels:
                try:
                    await ws.send_text(frame)
                except Exception:
                    dead.append(ws)

        for ws in dead:
            self.disconnect(ws)

    async def handle(self, ws: WebSocket) -> None:
        """Main loop for a single WebSocket connection.

        Reads incoming messages, handles subscribe commands, and forwards
        terminal input to the registered callback.
        """
        await self.connect(ws)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue

                channel = msg.get("channel")
                data = msg.get("data")

                if channel == "subscribe" and isinstance(data, list):
                    self.subscribe(ws, data)
                elif (
                    channel
                    and channel.startswith("terminal:")
                    and data
                    and self._input_callback
                ):
                    try:
                        await self._input_callback(channel, data)
                    except Exception:
                        logger.exception("Input callback error for %s", channel)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("WebSocket error")
        finally:
            self.disconnect(ws)

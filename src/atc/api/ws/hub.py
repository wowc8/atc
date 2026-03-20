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
        self._heartbeat_callback: Any | None = None
        self._subscribe_callback: Any | None = None
        self._resize_callback: Any | None = None

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def on_input(self, callback: Any) -> None:
        """Register a callback for terminal input from clients.

        Callback signature: ``async def cb(channel: str, data: str) -> None``
        """
        self._input_callback = callback

    def on_heartbeat(self, callback: Any) -> None:
        """Register a callback for heartbeat pings from agents.

        Callback signature: ``async def cb(session_id: str) -> None``
        """
        self._heartbeat_callback = callback

    def on_resize(self, callback: Any) -> None:
        """Register a callback for terminal resize events from clients.

        Callback signature: ``async def cb(channel: str, cols: int, rows: int) -> None``
        """
        self._resize_callback = callback

    def on_subscribe(self, callback: Any) -> None:
        """Register a callback invoked when a client subscribes to a channel.

        Callback signature: ``async def cb(ws: WebSocket, channel: str) -> None``

        Used to send initial terminal content when a client subscribes to a
        ``terminal:*`` channel.
        """
        self._subscribe_callback = callback

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

    async def send_to(self, ws: WebSocket, channel: str, data: Any) -> None:
        """Send a message to a specific client."""
        frame = json.dumps({"channel": channel, "data": data})
        try:
            await ws.send_text(frame)
        except Exception:
            self.disconnect(ws)

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
                    # Send initial content for terminal channels
                    if self._subscribe_callback:
                        for ch in data:
                            if ch.startswith("terminal:"):
                                try:
                                    await self._subscribe_callback(ws, ch)
                                except Exception:
                                    logger.debug("Subscribe callback error for %s", ch)
                elif channel == "heartbeat" and isinstance(data, dict) and self._heartbeat_callback:
                    session_id = data.get("session_id", "")
                    if session_id:
                        try:
                            await self._heartbeat_callback(session_id)
                        except Exception:
                            logger.exception("Heartbeat callback error for %s", session_id)
                elif (
                    channel
                    and channel.startswith("terminal:")
                    and msg.get("type") == "resize"
                    and isinstance(data, dict)
                    and self._resize_callback
                ):
                    cols = data.get("cols", 0)
                    rows = data.get("rows", 0)
                    if cols > 0 and rows > 0:
                        try:
                            await self._resize_callback(channel, cols, rows)
                        except Exception:
                            logger.debug("Resize callback error for %s", channel)
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

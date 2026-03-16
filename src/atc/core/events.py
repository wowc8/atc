"""In-process pub/sub event bus for ATC subsystem communication."""

from __future__ import annotations

import contextlib
import logging
from collections import defaultdict
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# Type alias for event handlers
EventHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class EventBus:
    """Async in-process pub/sub event bus.

    Subsystems publish events by name; registered handlers receive them
    asynchronously.  Handlers that raise are logged but do not block
    other subscribers.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._started = False

    def subscribe(self, event: str, handler: EventHandler) -> None:
        """Register *handler* to be called whenever *event* is published."""
        self._handlers[event].append(handler)

    def unsubscribe(self, event: str, handler: EventHandler) -> None:
        """Remove a previously registered handler."""
        with contextlib.suppress(ValueError):
            self._handlers[event].remove(handler)

    async def publish(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Dispatch *event* with *data* to all registered handlers."""
        payload = data or {}
        for handler in list(self._handlers.get(event, [])):
            try:
                await handler(payload)
            except Exception:
                logger.exception("Event handler failed for %s", event)

    async def start(self) -> None:
        """Mark the bus as started (future-proofing for queue-based dispatch)."""
        self._started = True

    async def stop(self) -> None:
        """Shut down the event bus."""
        self._started = False
        self._handlers.clear()

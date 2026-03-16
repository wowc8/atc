"""Unit tests for the EventBus."""

from __future__ import annotations

import pytest

from atc.core.events import EventBus


class TestEventBus:
    @pytest.fixture
    def bus(self) -> EventBus:
        return EventBus()

    async def test_publish_no_subscribers(self, bus: EventBus) -> None:
        """Publishing with no subscribers should not raise."""
        await bus.publish("some_event", {"key": "value"})

    async def test_subscribe_and_receive(self, bus: EventBus) -> None:
        received: list[dict] = []

        async def handler(data: dict) -> None:
            received.append(data)

        bus.subscribe("test_event", handler)
        await bus.publish("test_event", {"x": 1})

        assert len(received) == 1
        assert received[0] == {"x": 1}

    async def test_multiple_subscribers(self, bus: EventBus) -> None:
        results: list[str] = []

        async def handler_a(data: dict) -> None:
            results.append("a")

        async def handler_b(data: dict) -> None:
            results.append("b")

        bus.subscribe("ev", handler_a)
        bus.subscribe("ev", handler_b)
        await bus.publish("ev")

        assert sorted(results) == ["a", "b"]

    async def test_unsubscribe(self, bus: EventBus) -> None:
        received: list[dict] = []

        async def handler(data: dict) -> None:
            received.append(data)

        bus.subscribe("ev", handler)
        bus.unsubscribe("ev", handler)
        await bus.publish("ev", {"x": 1})

        assert len(received) == 0

    async def test_unsubscribe_nonexistent(self, bus: EventBus) -> None:
        """Unsubscribing a handler that was never registered should not raise."""

        async def handler(data: dict) -> None:
            pass

        bus.unsubscribe("ev", handler)  # no error

    async def test_handler_error_does_not_block_others(self, bus: EventBus) -> None:
        results: list[str] = []

        async def bad_handler(data: dict) -> None:
            raise RuntimeError("boom")

        async def good_handler(data: dict) -> None:
            results.append("ok")

        bus.subscribe("ev", bad_handler)
        bus.subscribe("ev", good_handler)
        await bus.publish("ev")

        assert results == ["ok"]

    async def test_publish_default_data(self, bus: EventBus) -> None:
        received: list[dict] = []

        async def handler(data: dict) -> None:
            received.append(data)

        bus.subscribe("ev", handler)
        await bus.publish("ev")  # no data arg

        assert received == [{}]

    async def test_start_stop(self, bus: EventBus) -> None:
        await bus.start()
        await bus.stop()
        # After stop, handlers are cleared
        assert len(bus._handlers) == 0

    async def test_events_are_isolated(self, bus: EventBus) -> None:
        a_results: list[str] = []
        b_results: list[str] = []

        async def handler_a(data: dict) -> None:
            a_results.append("a")

        async def handler_b(data: dict) -> None:
            b_results.append("b")

        bus.subscribe("event_a", handler_a)
        bus.subscribe("event_b", handler_b)

        await bus.publish("event_a")
        assert a_results == ["a"]
        assert b_results == []

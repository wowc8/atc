"""Unit tests for session state machine."""

from __future__ import annotations

import pytest

from atc.session.state_machine import (
    InvalidTransitionError,
    SessionStatus,
    VALID_TRANSITIONS,
    is_valid_transition,
    transition,
)


class TestSessionStatus:
    """Test the SessionStatus enum."""

    def test_values(self) -> None:
        assert SessionStatus.IDLE.value == "idle"
        assert SessionStatus.CONNECTING.value == "connecting"
        assert SessionStatus.WORKING.value == "working"
        assert SessionStatus.PAUSED.value == "paused"
        assert SessionStatus.WAITING.value == "waiting"
        assert SessionStatus.DISCONNECTED.value == "disconnected"
        assert SessionStatus.ERROR.value == "error"

    def test_from_string(self) -> None:
        assert SessionStatus("idle") == SessionStatus.IDLE
        assert SessionStatus("working") == SessionStatus.WORKING

    def test_all_statuses_have_transitions(self) -> None:
        """Every status must appear as a key in VALID_TRANSITIONS."""
        for status in SessionStatus:
            assert status in VALID_TRANSITIONS, f"{status} missing from VALID_TRANSITIONS"


class TestIsValidTransition:
    """Test the is_valid_transition function."""

    def test_idle_to_connecting(self) -> None:
        assert is_valid_transition(SessionStatus.IDLE, SessionStatus.CONNECTING)

    def test_idle_to_working(self) -> None:
        assert is_valid_transition(SessionStatus.IDLE, SessionStatus.WORKING)

    def test_connecting_to_idle(self) -> None:
        assert is_valid_transition(SessionStatus.CONNECTING, SessionStatus.IDLE)

    def test_connecting_to_error(self) -> None:
        assert is_valid_transition(SessionStatus.CONNECTING, SessionStatus.ERROR)

    def test_working_to_waiting(self) -> None:
        assert is_valid_transition(SessionStatus.WORKING, SessionStatus.WAITING)

    def test_working_to_paused(self) -> None:
        assert is_valid_transition(SessionStatus.WORKING, SessionStatus.PAUSED)

    def test_disconnected_to_connecting(self) -> None:
        assert is_valid_transition(SessionStatus.DISCONNECTED, SessionStatus.CONNECTING)

    def test_error_to_connecting(self) -> None:
        assert is_valid_transition(SessionStatus.ERROR, SessionStatus.CONNECTING)

    def test_invalid_idle_to_disconnected(self) -> None:
        assert not is_valid_transition(SessionStatus.IDLE, SessionStatus.DISCONNECTED)

    def test_invalid_paused_to_disconnected(self) -> None:
        assert not is_valid_transition(SessionStatus.PAUSED, SessionStatus.DISCONNECTED)

    def test_invalid_error_to_working(self) -> None:
        assert not is_valid_transition(SessionStatus.ERROR, SessionStatus.WORKING)

    def test_self_transition_not_allowed(self) -> None:
        """No status should transition to itself."""
        for status in SessionStatus:
            assert not is_valid_transition(status, status), f"{status} → {status} should be invalid"


class TestTransition:
    """Test the async transition function."""

    @pytest.mark.asyncio
    async def test_valid_transition(self) -> None:
        """Valid transitions should not raise."""
        await transition("sess-1", SessionStatus.IDLE, SessionStatus.CONNECTING)

    @pytest.mark.asyncio
    async def test_invalid_transition_raises(self) -> None:
        with pytest.raises(InvalidTransitionError) as exc_info:
            await transition("sess-1", SessionStatus.IDLE, SessionStatus.DISCONNECTED)
        assert exc_info.value.session_id == "sess-1"
        assert exc_info.value.current == SessionStatus.IDLE
        assert exc_info.value.target == SessionStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_event_published(self) -> None:
        """transition() should publish event when bus is provided."""
        from atc.core.events import EventBus

        bus = EventBus()
        received: list[dict] = []

        async def handler(data: dict) -> None:
            received.append(data)

        bus.subscribe("session_status_changed", handler)

        await transition("sess-2", SessionStatus.IDLE, SessionStatus.WORKING, bus)

        assert len(received) == 1
        assert received[0]["session_id"] == "sess-2"
        assert received[0]["previous_status"] == "idle"
        assert received[0]["new_status"] == "working"

    @pytest.mark.asyncio
    async def test_no_event_without_bus(self) -> None:
        """transition() should work fine with no event bus."""
        await transition("sess-3", SessionStatus.CONNECTING, SessionStatus.IDLE)


class TestInvalidTransitionError:
    def test_message(self) -> None:
        err = InvalidTransitionError("s1", SessionStatus.IDLE, SessionStatus.DISCONNECTED)
        assert "s1" in str(err)
        assert "idle" in str(err)
        assert "disconnected" in str(err)

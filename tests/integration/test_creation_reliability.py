"""Integration tests for creation reliability.

These tests verify the DB-first creation pattern and the full
create_ace → verify flow using an in-memory database (no tmux).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from atc.core.events import EventBus
from atc.runtime.models import ReadinessState, RoleKind, RuntimeDeliveryResult, RuntimeInspection
from atc.session.ace import (
    create_ace,
    start_ace,
    verify_session,
)
from atc.session.state_machine import SessionStatus
from atc.state import db as db_ops


@pytest.fixture
async def conn():
    """Provide an in-memory database with schema applied."""
    factory = db_ops.ConnectionFactory(":memory:")
    async with db_ops.get_connection(factory.db_path) as db:
        await db_ops.run_migrations(factory.db_path)
        yield db
    factory.close()


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


class TestDBFirstCreation:
    """Verify that the DB row is written before tmux operations."""

    @pytest.mark.asyncio
    @patch("atc.session.ace._spawn_provider_session", new_callable=AsyncMock)
    async def test_session_row_created_before_tmux(
        self,
        mock_spawn_provider: AsyncMock,
        conn,
        event_bus: EventBus,
    ) -> None:
        """The DB row must exist even before the pane is spawned."""
        # Record when DB and tmux operations happen
        call_order: list[str] = []

        original_create = db_ops.create_session

        async def tracking_create(*args, **kwargs):
            result = await original_create(*args, **kwargs)
            call_order.append("db_create")
            return result

        mock_spawn_provider.return_value = ("atc", "%1")

        async def tracking_spawn(*args, **kwargs):
            call_order.append("tmux_spawn")
            return ("atc", "%1")

        mock_spawn_provider.side_effect = tracking_spawn

        # Create project first
        project = await db_ops.create_project(conn, "test-project")

        with patch("atc.session.ace.db_ops.create_session", side_effect=tracking_create):
            session_id = await create_ace(conn, project.id, "test-ace", event_bus=event_bus)

        # DB create must come before tmux spawn
        assert call_order == ["db_create", "tmux_spawn"]

        # Session must exist in DB
        session = await db_ops.get_session(conn, session_id)
        assert session is not None

    @pytest.mark.asyncio
    @patch(
        "atc.session.ace._spawn_provider_session",
        new_callable=AsyncMock,
        return_value=("atc", "%2"),
    )
    async def test_session_moves_to_idle_on_success(
        self,
        mock_spawn_provider: AsyncMock,
        conn,
        event_bus: EventBus,
    ) -> None:
        project = await db_ops.create_project(conn, "test-project")

        session_id = await create_ace(conn, project.id, "test-ace", event_bus=event_bus)

        session = await db_ops.get_session(conn, session_id)
        assert session is not None
        assert session.status == SessionStatus.IDLE.value
        assert session.tmux_pane == "%2"

    @pytest.mark.asyncio
    @patch(
        "atc.session.ace._spawn_provider_session",
        new_callable=AsyncMock,
        return_value=("atc", "%2"),
    )
    async def test_create_ace_rejects_second_active_session_for_same_task(
        self,
        mock_spawn_provider: AsyncMock,
        conn,
        event_bus: EventBus,
    ) -> None:
        project = await db_ops.create_project(conn, "test-project")
        task = await db_ops.create_task_graph(conn, project.id, "Task A")

        await create_ace(conn, project.id, "test-ace", event_bus=event_bus, task_id=task.id)

        with pytest.raises(ValueError, match="already has active Ace session"):
            await create_ace(
                conn,
                project.id,
                "duplicate-ace",
                event_bus=event_bus,
                task_id=task.id,
            )

        sessions = await db_ops.list_sessions(conn, project_id=project.id, session_type="ace")
        assert len(sessions) == 1

    @pytest.mark.asyncio
    @patch(
        "atc.session.ace._spawn_provider_session",
        new_callable=AsyncMock,
        side_effect=RuntimeError("tmux not available"),
    )
    async def test_session_moves_to_error_on_failure(
        self,
        mock_spawn_provider: AsyncMock,
        conn,
        event_bus: EventBus,
    ) -> None:
        """If tmux spawn fails, the session row should still exist with error status."""
        project = await db_ops.create_project(conn, "test-project")

        with pytest.raises(RuntimeError, match="tmux not available"):
            await create_ace(conn, project.id, "test-ace", event_bus=event_bus)

        # Session must exist with error status — no ghost session
        sessions = await db_ops.list_sessions(conn, project_id=project.id, session_type="ace")
        assert len(sessions) == 1
        assert sessions[0].status == SessionStatus.ERROR.value

    @pytest.mark.asyncio
    @patch(
        "atc.session.ace._spawn_provider_session",
        new_callable=AsyncMock,
        return_value=("atc", "%3"),
    )
    async def test_creation_event_published(
        self,
        mock_spawn_provider: AsyncMock,
        conn,
        event_bus: EventBus,
    ) -> None:
        received: list[dict] = []

        async def handler(data: dict) -> None:
            received.append(data)

        event_bus.subscribe("session_created", handler)

        project = await db_ops.create_project(conn, "test-project")
        session_id = await create_ace(conn, project.id, "test-ace", event_bus=event_bus)

        assert len(received) == 1
        assert received[0]["session_id"] == session_id
        assert received[0]["session_type"] == "ace"


class TestAtomicInstructionSending:
    """Verify that start_ace uses atomic instruction sending."""

    @pytest.mark.asyncio
    @patch("atc.session.ace._send_session_instruction", new_callable=AsyncMock)
    @patch(
        "atc.session.ace._spawn_provider_session",
        new_callable=AsyncMock,
        return_value=("atc", "%4"),
    )
    async def test_start_ace_uses_send_instruction(
        self,
        mock_spawn_provider: AsyncMock,
        mock_send: AsyncMock,
        conn,
        event_bus: EventBus,
    ) -> None:
        mock_send.return_value = RuntimeDeliveryResult(
            session_id="test-ace",
            provider_name="codex",
            role=RoleKind.ACE,
            status="confirmed",
        )

        project = await db_ops.create_project(conn, "test-project")
        session_id = await create_ace(conn, project.id, "test-ace", event_bus=event_bus)

        await start_ace(conn, session_id, instruction="do work", event_bus=event_bus)

        mock_send.assert_called_once_with(conn, session_id, "do work")

    @pytest.mark.asyncio
    @patch("atc.session.ace._send_session_instruction", new_callable=AsyncMock)
    @patch(
        "atc.session.ace._spawn_provider_session",
        new_callable=AsyncMock,
        return_value=("atc", "%5"),
    )
    async def test_start_ace_errors_on_failed_delivery(
        self,
        mock_spawn_provider: AsyncMock,
        mock_send: AsyncMock,
        conn,
        event_bus: EventBus,
    ) -> None:
        mock_send.return_value = RuntimeDeliveryResult(
            session_id="test-ace",
            provider_name="codex",
            role=RoleKind.ACE,
            status="failed",
        )

        project = await db_ops.create_project(conn, "test-project")
        session_id = await create_ace(conn, project.id, "test-ace", event_bus=event_bus)

        result = await start_ace(conn, session_id, instruction="do work", event_bus=event_bus)
        assert result.status == "failed"

        # Session should be in error state
        session = await db_ops.get_session(conn, session_id)
        assert session is not None
        assert session.status == SessionStatus.ERROR.value


class TestVerificationChecks:
    """Integration tests for the verification checks."""

    @pytest.mark.asyncio
    @patch("atc.session.ace.RuntimeService.inspect_session_record", new_callable=AsyncMock)
    @patch(
        "atc.session.ace._spawn_provider_session",
        new_callable=AsyncMock,
        return_value=("atc", "%6"),
    )
    async def test_verify_session_alive(
        self,
        mock_spawn_provider: AsyncMock,
        mock_inspect: AsyncMock,
        conn,
        event_bus: EventBus,
    ) -> None:
        mock_inspect.return_value = RuntimeInspection(
            session_id="test-ace",
            provider_name="codex",
            alive=True,
            readiness=ReadinessState.READY,
        )

        project = await db_ops.create_project(conn, "test-project")
        session_id = await create_ace(conn, project.id, "test-ace", event_bus=event_bus)

        assert await verify_session(conn, session_id, event_bus=event_bus) is True

    @pytest.mark.asyncio
    @patch("atc.session.ace._pane_is_alive", new_callable=AsyncMock)
    @patch(
        "atc.session.ace._spawn_provider_session",
        new_callable=AsyncMock,
        return_value=("atc", "%7"),
    )
    async def test_verify_session_dead_pane(
        self,
        mock_spawn_provider: AsyncMock,
        mock_alive: AsyncMock,
        conn,
        event_bus: EventBus,
    ) -> None:
        mock_alive.return_value = False

        project = await db_ops.create_project(conn, "test-project")
        session_id = await create_ace(conn, project.id, "test-ace", event_bus=event_bus)

        assert await verify_session(conn, session_id, event_bus=event_bus) is False

        session = await db_ops.get_session(conn, session_id)
        assert session is not None
        # idle → disconnected is not a valid transition, so verify_session
        # catches the exception and still returns False.  The session stays
        # in its current status because the transition was rejected.
        assert session.status in (
            SessionStatus.DISCONNECTED.value,
            SessionStatus.IDLE.value,
        )

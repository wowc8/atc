from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from atc.orchestration.errors import OrchestrationException
from atc.orchestration.models import (
    CancelSessionRequest,
    ListSessionsRequest,
    OrchestrationRole,
    OrchestrationStatus,
    SendInstructionRequest,
    SessionSummary,
    SpawnAceRequest,
    SpawnLeaderRequest,
    WaitForSessionRequest,
)
from atc.orchestration.service import OrchestrationService
from atc.runtime.models import RoleKind, RuntimeDeliveryResult
from atc.state import db as db_ops
from atc.tower.controller import TowerBusyError, TowerState


class _FakeTowerController:
    def __init__(self, result: dict | None = None, exc: Exception | None = None) -> None:
        self._result = result or {}
        self._exc = exc
        self.calls: list[tuple[str, str]] = []

    async def submit_goal(self, project_id: str, goal: str) -> dict:
        self.calls.append((project_id, goal))
        if self._exc:
            raise self._exc
        return self._result


def _delivery_result(*, ok: bool = True, status: str = "accepted") -> RuntimeDeliveryResult:
    return RuntimeDeliveryResult(
        session_id="session-123",
        provider_name="codex",
        role=RoleKind.ACE,
        status=status,
        stage="submit_attempted",
        verdict="accepted" if ok else "failed",
        reason_code="submit_sent" if ok else "submit_failed",
    )


@pytest_asyncio.fixture()
async def db_conn(tmp_path: Path):
    db_path = tmp_path / "test.db"
    await db_ops.run_migrations(str(db_path))
    async with db_ops.get_connection(str(db_path)) as conn:
        yield conn


@pytest.mark.asyncio
async def test_get_session_not_found(db_conn) -> None:
    service = OrchestrationService(db_conn)
    with pytest.raises(OrchestrationException) as exc:
        await service.get_session("missing")
    assert exc.value.code.value == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_list_sessions_normalizes_role_and_status(db_conn) -> None:
    project = await db_ops.create_project(db_conn, "ATC")
    session = await db_ops.create_session(
        db_conn,
        project_id=project.id,
        session_type="manager",
        name="leader-ATC",
        status="idle",
    )
    leader = await db_ops.create_leader(db_conn, project.id, goal="Ship it")
    await db_conn.execute(
        "UPDATE leaders SET session_id = ?, goal = ?, status = 'managing' WHERE id = ?",
        (session.id, "Ship it", leader.id),
    )
    await db_conn.commit()

    service = OrchestrationService(db_conn)
    summaries = await service.list_sessions(ListSessionsRequest(role=OrchestrationRole.LEADER))
    assert len(summaries) == 1
    assert summaries[0].role == OrchestrationRole.LEADER
    assert summaries[0].status == OrchestrationStatus.READY
    assert summaries[0].goal == "Ship it"


@pytest.mark.asyncio
async def test_list_sessions_active_only_filters_failed(db_conn) -> None:
    project = await db_ops.create_project(db_conn, "ATC")
    await db_ops.create_session(
        db_conn,
        project_id=project.id,
        session_type="ace",
        name="ace-1",
        status="error",
    )
    await db_ops.create_session(
        db_conn,
        project_id=project.id,
        session_type="ace",
        name="ace-2",
        status="working",
    )
    service = OrchestrationService(db_conn)
    summaries = await service.list_sessions(ListSessionsRequest(active_only=True))
    assert len(summaries) == 1
    assert summaries[0].name == "ace-2"


@pytest.mark.asyncio
async def test_spawn_leader_wraps_tower_controller(db_conn) -> None:
    project = await db_ops.create_project(db_conn, "ATC")
    leader = await db_ops.create_leader(db_conn, project.id, goal="Ship it")
    session = await db_ops.create_session(
        db_conn,
        project_id=project.id,
        session_type="manager",
        name="leader-ATC",
        status="connecting",
    )
    await db_conn.execute(
        "UPDATE leaders SET session_id = ?, goal = ?, status = 'managing' WHERE id = ?",
        (session.id, "Ship it", leader.id),
    )
    await db_conn.commit()

    tower = _FakeTowerController(result={"leader_session_id": session.id})
    service = OrchestrationService(db_conn, tower_controller=tower)
    response = await service.spawn_leader(
        SpawnLeaderRequest(
            project_id=project.id,
            goal="Ship it",
            idempotency_key="goal-1",
        )
    )
    assert response.operation_id == "goal-1"
    assert response.session is not None
    assert response.session.status == OrchestrationStatus.STARTING
    assert tower.calls == [(project.id, "Ship it")]


@pytest.mark.asyncio
async def test_spawn_leader_maps_busy_error(db_conn) -> None:
    project = await db_ops.create_project(db_conn, "ATC")
    tower = _FakeTowerController(
        exc=TowerBusyError(state=TowerState.MANAGING, project_id=project.id, detail="at capacity")
    )
    service = OrchestrationService(db_conn, tower_controller=tower)
    with pytest.raises(OrchestrationException) as exc:
        await service.spawn_leader(
            SpawnLeaderRequest(project_id=project.id, goal="Ship it", idempotency_key="goal-2")
        )
    assert exc.value.code.value == "CONCURRENCY_LIMIT_REACHED"
    assert exc.value.retryable is True


@pytest.mark.asyncio
async def test_spawn_ace_creates_session_and_assigns_task(db_conn) -> None:
    project = await db_ops.create_project(db_conn, "ATC", repo_path="/tmp/atc-repo")
    task = await db_ops.create_task_graph(db_conn, project.id, "Task 1")
    fake_session_id = "session-123"
    fake_summary = SessionSummary(
        id=fake_session_id,
        role=OrchestrationRole.ACE,
        raw_session_type="ace",
        project_id=project.id,
        status=OrchestrationStatus.READY,
        raw_status="idle",
        name="ace-Task 1",
        created_at="now",
        updated_at="now",
    )
    with (
        patch(
            "atc.orchestration.service.ace_ops.create_ace",
            new=AsyncMock(return_value=fake_session_id),
        ),
        patch(
            "atc.orchestration.service._send_session_instruction",
            new=AsyncMock(return_value=_delivery_result()),
        ),
        patch.object(OrchestrationService, "get_session", new=AsyncMock(return_value=fake_summary)),
    ):
        service = OrchestrationService(db_conn)
        response = await service.spawn_ace(
            SpawnAceRequest(
                project_id=project.id,
                task_id=task.id,
                instruction="Start work",
                idempotency_key="assign-1",
                context={"task_title": "Task 1"},
            )
        )
    assignment = await db_ops.get_task_assignment(db_conn, "assign-1")
    assert response.operation_id == "assign-1"
    assert assignment is not None
    assert assignment.ace_session_id == fake_session_id


@pytest.mark.asyncio
async def test_send_instruction_returns_submitted_response(db_conn) -> None:
    project = await db_ops.create_project(db_conn, "ATC")
    session = await db_ops.create_session(
        db_conn,
        project_id=project.id,
        session_type="ace",
        name="ace-1",
        status="working",
    )
    service = OrchestrationService(db_conn)
    with patch(
        "atc.orchestration.service._send_session_instruction",
        new=AsyncMock(return_value=_delivery_result()),
    ) as mock_send:
        response = await service.send_instruction(
            SendInstructionRequest(
                session_id=session.id,
                instruction="Do the thing",
                idempotency_key="send-1",
            )
        )
    assert response.request_status == "submitted"
    assert response.delivery_state == "submitted"
    assert response.operation_id == "send-1"
    assert response.session is not None
    assert response.session.id == session.id
    mock_send.assert_awaited_once_with(db_conn, session.id, "Do the thing")


@pytest.mark.asyncio
async def test_send_instruction_reuses_idempotent_response(db_conn) -> None:
    project = await db_ops.create_project(db_conn, "ATC")
    session = await db_ops.create_session(
        db_conn,
        project_id=project.id,
        session_type="ace",
        name="ace-1",
        status="working",
    )
    service = OrchestrationService(db_conn)
    with patch(
        "atc.orchestration.service._send_session_instruction",
        new=AsyncMock(return_value=_delivery_result()),
    ) as mock_send:
        first = await service.send_instruction(
            SendInstructionRequest(
                session_id=session.id,
                instruction="Do the thing",
                idempotency_key="send-idempotent",
            )
        )
        second = await service.send_instruction(
            SendInstructionRequest(
                session_id=session.id,
                instruction="Do the thing again",
                idempotency_key="send-idempotent",
            )
        )

    assert first.operation_id == "send-idempotent"
    assert second.operation_id == "send-idempotent"
    assert second.request_status == "submitted"
    assert second.delivery_state == "submitted"
    mock_send.assert_awaited_once_with(db_conn, session.id, "Do the thing")


@pytest.mark.asyncio
async def test_send_instruction_maps_rejected_delivery(db_conn) -> None:
    project = await db_ops.create_project(db_conn, "ATC")
    session = await db_ops.create_session(
        db_conn,
        project_id=project.id,
        session_type="ace",
        name="ace-1",
        status="working",
    )
    service = OrchestrationService(db_conn)
    with patch(
        "atc.orchestration.service._send_session_instruction",
        new=AsyncMock(return_value=_delivery_result(ok=False, status="failed")),
    ):
        response = await service.send_instruction(
            SendInstructionRequest(
                session_id=session.id,
                instruction="Do the thing",
                idempotency_key="send-2",
            )
        )
    assert response.request_status == "failed"
    assert response.delivery_state == "failed"
    assert response.recovery == "inspect session health and delivery traces before retrying"


@pytest.mark.asyncio
async def test_wait_for_session_returns_when_status_matches(db_conn) -> None:
    project = await db_ops.create_project(db_conn, "ATC")
    session = await db_ops.create_session(
        db_conn,
        project_id=project.id,
        session_type="ace",
        name="ace-1",
        status="connecting",
    )
    service = OrchestrationService(db_conn)

    async def flip_status() -> None:
        await asyncio.sleep(0.05)
        await db_ops.update_session_status(db_conn, session.id, "idle")

    task = asyncio.create_task(flip_status())
    summary = await service.wait_for_session(
        WaitForSessionRequest(
            session_id=session.id,
            target_statuses=[OrchestrationStatus.READY],
            timeout_ms=1000,
        )
    )
    await task
    assert summary.id == session.id
    assert summary.status == OrchestrationStatus.READY


@pytest.mark.asyncio
async def test_wait_for_session_times_out(db_conn) -> None:
    project = await db_ops.create_project(db_conn, "ATC")
    session = await db_ops.create_session(
        db_conn,
        project_id=project.id,
        session_type="ace",
        name="ace-1",
        status="connecting",
    )
    service = OrchestrationService(db_conn)
    with pytest.raises(OrchestrationException) as exc:
        await service.wait_for_session(
            WaitForSessionRequest(
                session_id=session.id,
                target_statuses=[OrchestrationStatus.READY],
                timeout_ms=10,
            )
        )
    assert exc.value.code.value == "SESSION_NOT_READY"
    assert exc.value.retryable is True


@pytest.mark.asyncio
async def test_cancel_ace_soft_stop_returns_summary(db_conn) -> None:
    project = await db_ops.create_project(db_conn, "ATC")
    session = await db_ops.create_session(
        db_conn,
        project_id=project.id,
        session_type="ace",
        name="ace-1",
        status="working",
    )
    service = OrchestrationService(db_conn)
    summary = await service.cancel_session(CancelSessionRequest(session_id=session.id, force=False))
    assert summary is not None
    assert summary.status == OrchestrationStatus.BLOCKED


@pytest.mark.asyncio
async def test_cancel_ace_force_destroy_returns_none(db_conn) -> None:
    project = await db_ops.create_project(db_conn, "ATC")
    session = await db_ops.create_session(
        db_conn,
        project_id=project.id,
        session_type="ace",
        name="ace-1",
        status="working",
    )
    service = OrchestrationService(db_conn)
    result = await service.cancel_session(CancelSessionRequest(session_id=session.id, force=True))
    assert result is None
    assert await db_ops.get_session(db_conn, session.id) is None


@pytest.mark.asyncio
async def test_cancel_leader_unlinks_session(db_conn) -> None:
    project = await db_ops.create_project(db_conn, "ATC")
    leader = await db_ops.create_leader(db_conn, project.id, goal="Ship it")
    session = await db_ops.create_session(
        db_conn,
        project_id=project.id,
        session_type="manager",
        name="leader-ATC",
        status="working",
    )
    await db_conn.execute(
        "UPDATE leaders SET session_id = ?, status = 'managing' WHERE id = ?",
        (session.id, leader.id),
    )
    await db_conn.commit()
    service = OrchestrationService(db_conn)
    summary = await service.cancel_session(CancelSessionRequest(session_id=session.id, force=False))
    assert summary is not None
    assert summary.status == OrchestrationStatus.READY

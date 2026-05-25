from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from atc.orchestration.errors import OrchestrationException
from atc.orchestration.models import (
    ListSessionsRequest,
    OrchestrationRole,
    OrchestrationStatus,
    SendInstructionRequest,
    SpawnLeaderRequest,
)
from atc.orchestration.service import OrchestrationService
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
async def test_send_instruction_returns_accepted_response(db_conn) -> None:
    project = await db_ops.create_project(db_conn, "ATC")
    session = await db_ops.create_session(
        db_conn,
        project_id=project.id,
        session_type="ace",
        name="ace-1",
        status="working",
    )
    service = OrchestrationService(db_conn)
    with patch('atc.orchestration.service._send_session_instruction', new=AsyncMock(return_value=True)) as mock_send:
        response = await service.send_instruction(
            SendInstructionRequest(
                session_id=session.id,
                instruction='Do the thing',
                idempotency_key='send-1',
            )
        )
    assert response.request_status == 'accepted'
    assert response.operation_id == 'send-1'
    assert response.session is not None
    assert response.session.id == session.id
    mock_send.assert_awaited_once_with(db_conn, session.id, 'Do the thing')


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
    with patch('atc.orchestration.service._send_session_instruction', new=AsyncMock(return_value=False)):
        with pytest.raises(OrchestrationException) as exc:
            await service.send_instruction(
                SendInstructionRequest(
                    session_id=session.id,
                    instruction='Do the thing',
                    idempotency_key='send-2',
                )
            )
    assert exc.value.code.value == 'SESSION_NOT_READY'
    assert exc.value.retryable is True

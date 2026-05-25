from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from atc.orchestration.errors import OrchestrationException
from atc.orchestration.models import OrchestrationRole, OrchestrationStatus, SessionSummary, SpawnAceRequest, SpawnLeaderRequest
from atc.orchestration.service import OrchestrationService
from atc.state import db as db_ops


class _FakeTowerController:
    def __init__(self, result: dict | None = None) -> None:
        self._result = result or {}
        self.calls: list[tuple[str, str]] = []

    async def submit_goal(self, project_id: str, goal: str) -> dict:
        self.calls.append((project_id, goal))
        return self._result


@pytest_asyncio.fixture()
async def db_conn(tmp_path: Path):
    db_path = tmp_path / 'test.db'
    await db_ops.run_migrations(str(db_path))
    async with db_ops.get_connection(str(db_path)) as conn:
        yield conn


@pytest.mark.asyncio
async def test_spawn_leader_reuses_persisted_operation(db_conn) -> None:
    project = await db_ops.create_project(db_conn, 'ATC')
    summary = SessionSummary(
        id='leader-1', role=OrchestrationRole.LEADER, raw_session_type='manager', project_id=project.id,
        status=OrchestrationStatus.READY, raw_status='idle', name='leader-ATC', created_at='now', updated_at='now'
    )
    response_json = {'request_status': 'accepted', 'operation_id': 'goal-1', 'session': summary.model_dump()}
    await db_ops.create_orchestration_operation(
        db_conn, 'goal-1', 'spawn_leader', '{"project_id":"x"}', session_id='leader-1', response_payload=__import__('json').dumps(response_json)
    )
    tower = _FakeTowerController(result={'leader_session_id': 'leader-1'})
    service = OrchestrationService(db_conn, tower_controller=tower)
    response = await service.spawn_leader(SpawnLeaderRequest(project_id=project.id, goal='Ship it', idempotency_key='goal-1'))
    assert response.operation_id == 'goal-1'
    assert tower.calls == []


@pytest.mark.asyncio
async def test_spawn_ace_reuses_persisted_operation(db_conn) -> None:
    project = await db_ops.create_project(db_conn, 'ATC')
    summary = SessionSummary(
        id='ace-1', role=OrchestrationRole.ACE, raw_session_type='ace', project_id=project.id,
        status=OrchestrationStatus.READY, raw_status='idle', name='ace-1', created_at='now', updated_at='now'
    )
    response_json = {'request_status': 'accepted', 'operation_id': 'assign-1', 'session': summary.model_dump()}
    await db_ops.create_orchestration_operation(
        db_conn, 'assign-1', 'spawn_ace', '{"project_id":"x"}', session_id='ace-1', response_payload=__import__('json').dumps(response_json)
    )
    service = OrchestrationService(db_conn)
    response = await service.spawn_ace(SpawnAceRequest(project_id=project.id, instruction='Go', idempotency_key='assign-1'))
    assert response.operation_id == 'assign-1'


@pytest.mark.asyncio
async def test_idempotency_conflict_across_operation_types(db_conn) -> None:
    project = await db_ops.create_project(db_conn, 'ATC')
    await db_ops.create_orchestration_operation(db_conn, 'dup-1', 'spawn_leader', '{}')
    service = OrchestrationService(db_conn)
    with pytest.raises(OrchestrationException) as exc:
        await service.spawn_ace(SpawnAceRequest(project_id=project.id, instruction='Go', idempotency_key='dup-1'))
    assert exc.value.code.value == 'IDEMPOTENCY_CONFLICT'

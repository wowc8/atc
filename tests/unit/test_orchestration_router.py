from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from atc.api.app import create_app
from atc.orchestration.models import OrchestrationRole, OrchestrationStatus, SessionSummary
from atc.state import db as db_ops


class _FakeTowerController:
    def __init__(self, result: dict | None = None) -> None:
        self._result = result or {}
        self.calls: list[tuple[str, str]] = []

    async def submit_goal(self, project_id: str, goal: str) -> dict:
        self.calls.append((project_id, goal))
        return self._result


@pytest_asyncio.fixture()
async def app_and_db(tmp_path: Path):
    db_path = tmp_path / 'test.db'
    await db_ops.run_migrations(str(db_path))
    async with db_ops.get_connection(str(db_path)) as conn:
        app = create_app()
        app.state.db = conn
        app.state.tower_controller = _FakeTowerController()
        yield app, conn


@pytest.mark.asyncio
async def test_get_orchestration_session_not_found(app_and_db) -> None:
    app, _conn = app_and_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        response = await client.get('/api/orchestration/sessions/missing')
    assert response.status_code == 404
    assert response.json()['detail']['code'] == 'SESSION_NOT_FOUND'


@pytest.mark.asyncio
async def test_list_orchestration_sessions(app_and_db) -> None:
    app, conn = app_and_db
    project = await db_ops.create_project(conn, 'ATC')
    await db_ops.create_session(
        conn,
        project_id=project.id,
        session_type='ace',
        name='ace-1',
        status='working',
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        response = await client.get('/api/orchestration/sessions')
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]['role'] == 'ace'
    assert body[0]['status'] == 'running'


@pytest.mark.asyncio
async def test_spawn_orchestration_leader(app_and_db) -> None:
    app, conn = app_and_db
    project = await db_ops.create_project(conn, 'ATC')
    leader = await db_ops.create_leader(conn, project.id, goal='Ship it')
    session = await db_ops.create_session(
        conn,
        project_id=project.id,
        session_type='manager',
        name='leader-ATC',
        status='connecting',
    )
    await conn.execute(
        "UPDATE leaders SET session_id = ?, goal = ?, status = 'managing' WHERE id = ?",
        (session.id, 'Ship it', leader.id),
    )
    await conn.commit()
    app.state.tower_controller = _FakeTowerController(result={'leader_session_id': session.id})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        response = await client.post(
            '/api/orchestration/leaders',
            json={
                'project_id': project.id,
                'goal': 'Ship it',
                'idempotency_key': 'goal-1',
            },
        )
    assert response.status_code == 202
    body = response.json()
    assert body['operation_id'] == 'goal-1'
    assert body['session']['role'] == 'leader'
    assert body['session']['status'] == 'starting'


@pytest.mark.asyncio
async def test_spawn_orchestration_ace(app_and_db) -> None:
    app, conn = app_and_db
    project = await db_ops.create_project(conn, 'ATC', repo_path='/tmp/atc-repo')
    task = await db_ops.create_task_graph(conn, project.id, 'Task 1')
    fake_summary = SessionSummary(
        id='session-123',
        role=OrchestrationRole.ACE,
        raw_session_type='ace',
        project_id=project.id,
        status=OrchestrationStatus.READY,
        raw_status='idle',
        name='ace-Task 1',
        created_at='now',
        updated_at='now',
    )
    with patch('atc.orchestration.service.ace_ops.create_ace', new=AsyncMock(return_value='session-123')), \
         patch('atc.orchestration.service._send_session_instruction', new=AsyncMock(return_value=True)), \
         patch('atc.orchestration.service.OrchestrationService.get_session', new=AsyncMock(return_value=fake_summary)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url='http://test') as client:
            response = await client.post(
                '/api/orchestration/aces',
                json={
                    'project_id': project.id,
                    'task_id': task.id,
                    'instruction': 'Start work',
                    'idempotency_key': 'assign-1',
                    'context': {'task_title': 'Task 1'},
                },
            )
    assert response.status_code == 202
    body = response.json()
    assert body['operation_id'] == 'assign-1'
    assert body['session']['id'] == 'session-123'


@pytest.mark.asyncio
async def test_send_instruction_route(app_and_db) -> None:
    app, conn = app_and_db
    project = await db_ops.create_project(conn, 'ATC')
    session = await db_ops.create_session(
        conn,
        project_id=project.id,
        session_type='ace',
        name='ace-1',
        status='working',
    )
    transport = ASGITransport(app=app)
    with patch('atc.orchestration.service._send_session_instruction', new=AsyncMock(return_value=True)):
        async with AsyncClient(transport=transport, base_url='http://test') as client:
            response = await client.post(
                f'/api/orchestration/sessions/{session.id}/instruction',
                json={
                    'session_id': 'ignored-by-route',
                    'instruction': 'Do the thing',
                    'idempotency_key': 'send-1',
                },
            )
    assert response.status_code == 202
    body = response.json()
    assert body['operation_id'] == 'send-1'
    assert body['session']['id'] == session.id


@pytest.mark.asyncio
async def test_wait_for_session_route(app_and_db) -> None:
    app, conn = app_and_db
    project = await db_ops.create_project(conn, 'ATC')
    session = await db_ops.create_session(
        conn,
        project_id=project.id,
        session_type='ace',
        name='ace-1',
        status='idle',
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as client:
        response = await client.post(
            f'/api/orchestration/sessions/{session.id}/wait',
            json={
                'session_id': 'ignored-by-route',
                'target_statuses': ['ready'],
                'timeout_ms': 100,
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body['id'] == session.id
    assert body['status'] == 'ready'

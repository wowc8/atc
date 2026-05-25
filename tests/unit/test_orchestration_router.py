from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from atc.api.app import create_app
from atc.state import db as db_ops
from atc.tower.controller import TowerState


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

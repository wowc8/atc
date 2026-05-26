from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from atc.orchestration.service import OrchestrationService
from atc.state import db as db_ops


@pytest_asyncio.fixture()
async def db_conn(tmp_path: Path):
    db_path = tmp_path / 'test.db'
    await db_ops.run_migrations(str(db_path))
    async with db_ops.get_connection(str(db_path)) as conn:
        yield conn


@pytest.mark.asyncio
async def test_list_operations_returns_records(db_conn) -> None:
    await db_ops.create_orchestration_operation(
        db_conn,
        'op-1',
        'spawn_leader',
        json.dumps({'project_id': 'p1'}),
        session_id='s1',
        response_payload=json.dumps({'ok': True}),
    )
    service = OrchestrationService(db_conn)
    records = await service.list_operations()
    assert len(records) == 1
    assert records[0].operation_id == 'op-1'
    assert records[0].request_payload['project_id'] == 'p1'


@pytest.mark.asyncio
async def test_get_operation_returns_record(db_conn) -> None:
    await db_ops.create_orchestration_operation(
        db_conn,
        'op-1',
        'spawn_leader',
        json.dumps({'project_id': 'p1'}),
        session_id='s1',
    )
    service = OrchestrationService(db_conn)
    record = await service.get_operation('op-1')
    assert record.operation_id == 'op-1'
    assert record.session_id == 's1'

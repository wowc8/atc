from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from atc.mcp.server import MCPServer
from atc.orchestration.models import OperationAcceptedResponse, OrchestrationRole, OrchestrationStatus, SessionSummary


@pytest.mark.asyncio
async def test_list_tools_includes_orchestration_surface() -> None:
    service = AsyncMock()
    server = MCPServer(service)
    names = [tool['name'] for tool in server.list_tools()]
    assert names == [
        'list_sessions',
        'get_session',
        'list_operations',
        'get_operation',
        'list_session_events',
        'spawn_leader',
        'spawn_ace',
        'send_instruction',
        'wait_for_session',
        'cancel_session',
    ]
    assert all('inputSchema' in tool for tool in server.list_tools())


@pytest.mark.asyncio
async def test_call_tool_delegates_spawn_leader() -> None:
    summary = SessionSummary(
        id='leader-1', role=OrchestrationRole.LEADER, raw_session_type='manager', project_id='p1',
        status=OrchestrationStatus.READY, raw_status='idle', name='leader-ATC', created_at='now', updated_at='now'
    )
    service = AsyncMock()
    service.spawn_leader.return_value = OperationAcceptedResponse(
        request_status='accepted', operation_id='goal-1', session=summary
    )
    server = MCPServer(service)
    result = await server.call_tool('spawn_leader', {
        'project_id': 'p1',
        'goal': 'Ship it',
        'idempotency_key': 'goal-1',
    })
    assert result['operation_id'] == 'goal-1'
    service.spawn_leader.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_tool_delegates_cancel_session() -> None:
    summary = SessionSummary(
        id='ace-1', role=OrchestrationRole.ACE, raw_session_type='ace', project_id='p1',
        status=OrchestrationStatus.BLOCKED, raw_status='paused', name='ace-1', created_at='now', updated_at='now'
    )
    service = AsyncMock()
    service.cancel_session.return_value = summary
    server = MCPServer(service)
    result = await server.call_tool('cancel_session', {
        'session_id': 'ace-1',
        'force': False,
    })
    assert result['id'] == 'ace-1'
    service.cancel_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_tool_delegates_list_operations() -> None:
    service = AsyncMock()
    service.list_operations.return_value = []
    server = MCPServer(service)
    result = await server.call_tool('list_operations', {'limit': 5})
    assert result == []
    service.list_operations.assert_awaited_once()

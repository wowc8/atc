from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock

import pytest

from atc.mcp.server import MCPServer, MCPStdioServer
from atc.orchestration.errors import OrchestrationErrorCode, OrchestrationException
from atc.orchestration.models import OperationAcceptedResponse, OrchestrationRole, OrchestrationStatus, SessionSummary


@pytest.mark.asyncio
async def test_stdio_server_lists_tools() -> None:
    service = AsyncMock()
    output = io.StringIO()
    server = MCPStdioServer(MCPServer(service), stdin=io.StringIO('{"id":1,"method":"tools/list"}\n'), stdout=output)
    assert await server.run_once() is True
    payload = json.loads(output.getvalue())
    assert payload['jsonrpc'] == '2.0'
    assert payload['id'] == 1
    assert 'list_sessions' in [tool['name'] for tool in payload['result']['tools']]


@pytest.mark.asyncio
async def test_stdio_server_calls_tool() -> None:
    summary = SessionSummary(
        id='leader-1', role=OrchestrationRole.LEADER, raw_session_type='manager', project_id='p1',
        status=OrchestrationStatus.READY, raw_status='idle', name='leader-ATC', created_at='now', updated_at='now'
    )
    service = AsyncMock()
    service.spawn_leader.return_value = OperationAcceptedResponse(
        request_status='accepted', operation_id='goal-1', session=summary
    )
    input_data = io.StringIO('{"id":"abc","method":"tools/call","params":{"name":"spawn_leader","arguments":{"project_id":"p1","goal":"Ship it","idempotency_key":"goal-1"}}}\n')
    output_data = io.StringIO()
    server = MCPStdioServer(MCPServer(service), stdin=input_data, stdout=output_data)
    assert await server.run_once() is True
    payload = json.loads(output_data.getvalue())
    assert payload['id'] == 'abc'
    assert payload['result']['content']['operation_id'] == 'goal-1'
    service.spawn_leader.assert_awaited_once()


@pytest.mark.asyncio
async def test_stdio_server_returns_error_envelope() -> None:
    service = AsyncMock()
    service.get_session.side_effect = OrchestrationException(
        OrchestrationErrorCode.SESSION_NOT_FOUND,
        'missing session',
        retryable=False,
        details={'session_id': 'missing'},
    )
    input_data = io.StringIO('{"id":7,"method":"tools/call","params":{"name":"get_session","arguments":{"session_id":"missing"}}}\n')
    output_data = io.StringIO()
    server = MCPStdioServer(MCPServer(service), stdin=input_data, stdout=output_data)
    assert await server.run_once() is True
    payload = json.loads(output_data.getvalue())
    assert payload['id'] == 7
    assert payload['error']['code'] == -32001
    assert payload['error']['data']['code'] == 'session_not_found'


@pytest.mark.asyncio
async def test_stdio_server_eof_returns_false() -> None:
    service = AsyncMock()
    server = MCPStdioServer(MCPServer(service), stdin=io.StringIO(''), stdout=io.StringIO())
    assert await server.run_once() is False

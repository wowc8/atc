from __future__ import annotations

import io
from unittest.mock import AsyncMock

import pytest

from atc.mcp.server import MCPServer, MCPStdioServer
from atc.orchestration.models import OperationAcceptedResponse, OrchestrationRole, OrchestrationStatus, SessionSummary


@pytest.mark.asyncio
async def test_stdio_server_lists_tools() -> None:
    service = AsyncMock()
    server = MCPStdioServer(MCPServer(service), stdin=io.StringIO('{"method":"tools/list"}\n'), stdout=io.StringIO())
    assert await server.run_once() is True
    output = server._stdout.getvalue()
    assert 'list_sessions' in output
    assert 'cancel_session' in output


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
    input_data = io.StringIO('{"method":"tools/call","params":{"name":"spawn_leader","arguments":{"project_id":"p1","goal":"Ship it","idempotency_key":"goal-1"}}}\n')
    output_data = io.StringIO()
    server = MCPStdioServer(MCPServer(service), stdin=input_data, stdout=output_data)
    assert await server.run_once() is True
    assert 'goal-1' in output_data.getvalue()
    service.spawn_leader.assert_awaited_once()


@pytest.mark.asyncio
async def test_stdio_server_eof_returns_false() -> None:
    service = AsyncMock()
    server = MCPStdioServer(MCPServer(service), stdin=io.StringIO(''), stdout=io.StringIO())
    assert await server.run_once() is False

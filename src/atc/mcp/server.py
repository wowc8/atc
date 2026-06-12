from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any, TextIO

from atc.orchestration.errors import OrchestrationException
from atc.orchestration.models import (
    CancelSessionRequest,
    ListOperationsRequest,
    ListSessionsRequest,
    SendInstructionRequest,
    SpawnAceRequest,
    SpawnLeaderRequest,
    WaitForSessionRequest,
)

if TYPE_CHECKING:
    from atc.orchestration.service import OrchestrationService


class MCPServer:
    """Minimal MCP-facing adapter over the orchestration service."""

    def __init__(self, service: OrchestrationService) -> None:
        self._service = service

    def server_info(self) -> dict[str, Any]:
        return {
            "name": "atc-mcp",
            "version": "0.1.0",
        }

    def capabilities(self) -> dict[str, Any]:
        return {
            "tools": {"listChanged": False},
        }

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "list_sessions",
                "description": "List normalized orchestration sessions",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "role": {"type": "string"},
                        "active_only": {"type": "boolean"},
                        "limit": {"type": "integer"},
                    },
                },
            },
            {
                "name": "get_session",
                "description": "Fetch a normalized orchestration session",
                "inputSchema": {
                    "type": "object",
                    "required": ["session_id"],
                    "properties": {"session_id": {"type": "string"}},
                },
            },
            {
                "name": "list_operations",
                "description": "List orchestration operations/history",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "operation_type": {"type": "string"},
                        "session_id": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            },
            {
                "name": "get_operation",
                "description": "Fetch a single orchestration operation",
                "inputSchema": {
                    "type": "object",
                    "required": ["operation_id"],
                    "properties": {"operation_id": {"type": "string"}},
                },
            },
            {
                "name": "list_session_events",
                "description": "List app events for a session",
                "inputSchema": {
                    "type": "object",
                    "required": ["session_id"],
                    "properties": {"session_id": {"type": "string"}, "limit": {"type": "integer"}},
                },
            },
            {
                "name": "spawn_leader",
                "description": (
                    "Spawn a leader through Tower orchestration; "
                    "queued is not proof the Leader acted"
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["project_id", "goal", "idempotency_key"],
                },
            },
            {
                "name": "spawn_ace",
                "description": (
                    "Spawn an ace through orchestration and report "
                    "queued/submitted/blocked/failed delivery state"
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["project_id", "instruction", "idempotency_key"],
                },
            },
            {
                "name": "send_instruction",
                "description": (
                    "Send an instruction and report submitted/blocked/failed "
                    "delivery state without claiming provider acceptance"
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["session_id", "instruction", "idempotency_key"],
                },
            },
            {
                "name": "wait_for_session",
                "description": "Wait for a session to reach target orchestration statuses",
                "inputSchema": {"type": "object", "required": ["session_id", "target_statuses"]},
            },
            {
                "name": "cancel_session",
                "description": "Cancel or stop an orchestration session",
                "inputSchema": {"type": "object", "required": ["session_id"]},
            },
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "list_sessions":
            result = await self._service.list_sessions(
                ListSessionsRequest.model_validate(arguments)
            )
            return [item.model_dump(mode="json") for item in result]
        if name == "get_session":
            result = await self._service.get_session(arguments["session_id"])
            return result.model_dump(mode="json")
        if name == "list_operations":
            result = await self._service.list_operations(
                ListOperationsRequest.model_validate(arguments)
            )
            return [item.model_dump(mode="json") for item in result]
        if name == "get_operation":
            result = await self._service.get_operation(arguments["operation_id"])
            return result.model_dump(mode="json")
        if name == "list_session_events":
            result = await self._service.list_session_events(
                arguments["session_id"], limit=arguments.get("limit")
            )
            return [item.model_dump(mode="json") for item in result]
        if name == "spawn_leader":
            result = await self._service.spawn_leader(SpawnLeaderRequest.model_validate(arguments))
            return result.model_dump(mode="json")
        if name == "spawn_ace":
            result = await self._service.spawn_ace(SpawnAceRequest.model_validate(arguments))
            return result.model_dump(mode="json")
        if name == "send_instruction":
            result = await self._service.send_instruction(
                SendInstructionRequest.model_validate(arguments)
            )
            return result.model_dump(mode="json")
        if name == "wait_for_session":
            result = await self._service.wait_for_session(
                WaitForSessionRequest.model_validate(arguments)
            )
            return result.model_dump(mode="json")
        if name == "cancel_session":
            result = await self._service.cancel_session(
                CancelSessionRequest.model_validate(arguments)
            )
            return None if result is None else result.model_dump(mode="json")
        raise ValueError(f"Unknown MCP tool: {name}")


class MCPStdioServer:
    def __init__(
        self, server: MCPServer, *, stdin: TextIO | None = None, stdout: TextIO | None = None
    ) -> None:
        self._server = server
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout
        self._initialized = False

    def _success_response(self, request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _error_response(
        self, request_id: Any, code: int, message: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
        if data is not None:
            payload["error"]["data"] = data
        return payload

    async def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        method = message.get("method")
        try:
            if method == "initialize":
                self._initialized = True
                return self._success_response(
                    request_id,
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": self._server.capabilities(),
                        "serverInfo": self._server.server_info(),
                    },
                )
            if method == "notifications/initialized":
                self._initialized = True
                return None
            if not self._initialized:
                return self._error_response(request_id, -32002, "Server not initialized")
            if method == "tools/list":
                return self._success_response(request_id, {"tools": self._server.list_tools()})
            if method == "tools/call":
                params = message.get("params") or {}
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if not name:
                    return self._error_response(
                        request_id, -32602, "tools/call requires params.name"
                    )
                result = await self._server.call_tool(name, arguments)
                return self._success_response(request_id, {"content": result})
            return self._error_response(request_id, -32601, f"Unknown MCP method: {method}")
        except OrchestrationException as exc:
            return self._error_response(
                request_id,
                -32001,
                exc.message,
                {
                    "code": str(exc.code.value).lower(),
                    "retryable": exc.retryable,
                    "details": exc.details,
                },
            )
        except ValueError as exc:
            return self._error_response(request_id, -32602, str(exc))
        except Exception as exc:
            return self._error_response(request_id, -32000, str(exc))

    async def run_once(self) -> bool:
        line = self._stdin.readline()
        if line == "":
            return False
        line = line.strip()
        if not line:
            return True
        response = await self.handle_message(json.loads(line))
        if response is not None:
            self._stdout.write(json.dumps(response) + "\n")
            self._stdout.flush()
        return True

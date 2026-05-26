from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from atc.orchestration.models import (
    CancelSessionRequest,
    ListSessionsRequest,
    SendInstructionRequest,
    SpawnAceRequest,
    SpawnLeaderRequest,
    WaitForSessionRequest,
)
from atc.orchestration.service import OrchestrationService


class MCPServer:
    """Minimal MCP-facing adapter over the orchestration service.

    This is intentionally not a full protocol transport yet. It is the first
    contract slice that defines the MCP tool surface ATC intends to expose.
    """

    def __init__(self, service: OrchestrationService) -> None:
        self._service = service

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": "list_sessions", "description": "List normalized orchestration sessions"},
            {"name": "get_session", "description": "Fetch a normalized orchestration session"},
            {"name": "spawn_leader", "description": "Spawn a leader through Tower orchestration"},
            {"name": "spawn_ace", "description": "Spawn an ace through orchestration"},
            {"name": "send_instruction", "description": "Send an instruction to an orchestration session"},
            {"name": "wait_for_session", "description": "Wait for a session to reach target orchestration statuses"},
            {"name": "cancel_session", "description": "Cancel or stop an orchestration session"},
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "list_sessions":
            result = await self._service.list_sessions(ListSessionsRequest.model_validate(arguments))
            return {"content": [item.model_dump(mode="json") for item in result]}
        if name == "get_session":
            result = await self._service.get_session(arguments["session_id"])
            return {"content": result.model_dump(mode="json")}
        if name == "spawn_leader":
            result = await self._service.spawn_leader(SpawnLeaderRequest.model_validate(arguments))
            return {"content": result.model_dump(mode="json")}
        if name == "spawn_ace":
            result = await self._service.spawn_ace(SpawnAceRequest.model_validate(arguments))
            return {"content": result.model_dump(mode="json")}
        if name == "send_instruction":
            result = await self._service.send_instruction(SendInstructionRequest.model_validate(arguments))
            return {"content": result.model_dump(mode="json")}
        if name == "wait_for_session":
            result = await self._service.wait_for_session(WaitForSessionRequest.model_validate(arguments))
            return {"content": result.model_dump(mode="json")}
        if name == "cancel_session":
            result = await self._service.cancel_session(CancelSessionRequest.model_validate(arguments))
            return {"content": None if result is None else result.model_dump(mode="json")}
        raise ValueError(f"Unknown MCP tool: {name}")


class MCPStdioServer:
    """Tiny stdio transport for the current MCP adapter.

    Input lines are JSON objects with:
    - {"method": "tools/list"}
    - {"method": "tools/call", "params": {"name": ..., "arguments": {...}}}

    Output is one JSON object per line.
    """

    def __init__(self, server: MCPServer, *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        self._server = server
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout

    async def handle_message(self, message: dict[str, Any]) -> dict[str, Any]:
        method = message.get("method")
        if method == "tools/list":
            return {"tools": self._server.list_tools()}
        if method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not name:
                raise ValueError("tools/call requires params.name")
            return await self._server.call_tool(name, arguments)
        raise ValueError(f"Unknown MCP method: {method}")

    async def run_once(self) -> bool:
        line = self._stdin.readline()
        if line == "":
            return False
        line = line.strip()
        if not line:
            return True
        response = await self.handle_message(json.loads(line))
        self._stdout.write(json.dumps(response) + "\n")
        self._stdout.flush()
        return True

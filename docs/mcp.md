# MCP Integration Plan

ATC's MCP layer sits on top of the orchestration boundary, not directly on session, Tower, or tmux internals.

## Principles

- MCP tools speak in orchestration models, roles, statuses, and errors.
- MCP handlers delegate to `atc.orchestration.service.OrchestrationService`.
- The orchestration layer remains the normalization boundary between ATC product internals and external control surfaces.
- Early MCP slices should prefer a stable thin contract over pretending the full protocol/tool catalog is complete.

## Current Surface

ATC now ships an `atc-mcp` stdio entrypoint.

```bash
atc-mcp
```

The current line-delimited JSON request surface supports:

- `tools/list`
- `tools/call`

Available orchestration-backed tools:

- `list_sessions`
- `get_session`
- `list_operations`
- `get_operation`
- `list_session_events`
- `spawn_leader`
- `spawn_ace`
- `send_instruction`
- `wait_for_session`
- `cancel_session`

## Example Session

List tools:

```json
{"id":1,"method":"tools/list"}
```

Response:

```json
{"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"list_sessions","description":"List normalized orchestration sessions","inputSchema":{"type":"object","properties":{"project_id":{"type":"string"},"role":{"type":"string"},"active_only":{"type":"boolean"},"limit":{"type":"integer"}}}}]}}
```

Call a tool:

```json
{"id":2,"method":"tools/call","params":{"name":"list_operations","arguments":{"limit":10}}}
```

Response:

```json
{"jsonrpc":"2.0","id":2,"result":{"content":[{"operation_id":"goal-123","operation_type":"spawn_leader","session_id":"leader-123","status":"accepted","request_payload":{"project_id":"proj-1","goal":"Ship MCP"},"response_payload":{"request_status":"accepted"},"created_at":"2026-05-26T00:00:00Z","updated_at":"2026-05-26T00:00:00Z"}]}}
```

Error response shape:

```json
{"jsonrpc":"2.0","id":3,"error":{"code":-32001,"message":"Session missing not found","data":{"code":"session_not_found","retryable":false,"details":{"session_id":"missing"}}}}
```

## Current Scope Notes

- `wait_for_session` is still polling-based under the hood.
- Operation history currently comes from the persisted `orchestration_operations` table.
- Session event history currently reuses app events filtered by `session_id`.
- The current stdio layer is JSON-RPC-like and useful for real clients, but it is still a deliberately thin MCP transport rather than a fully exhaustive spec implementation.

## Recommended Next Layers

- stricter MCP spec alignment / handshake polish
- live subscriptions or streamed session events
- richer orchestration event sourcing under the history surface

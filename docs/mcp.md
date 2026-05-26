# MCP Integration Plan

ATC's MCP layer should sit on top of the orchestration boundary, not call session/tower internals directly.

## Principles

- MCP tools speak in orchestration models, roles, statuses, and errors.
- MCP handlers should delegate to `atc.orchestration.service.OrchestrationService`.
- The orchestration layer remains the normalization boundary between ATC product internals and external control surfaces.
- Early MCP slices should prefer a stable skeleton over pretending the full protocol/tool catalog is complete.

## Initial Tool Mapping

The first MCP server slice should expose these orchestration-backed operations:

- `list_sessions`
- `get_session`
- `spawn_leader`
- `spawn_ace`
- `send_instruction`
- `wait_for_session`
- `cancel_session`

## Response Shape

The MCP server should return JSON-serializable content based on orchestration models:

- `SessionSummary`
- `OperationAcceptedResponse`
- orchestration error envelopes

## Non-Goals for First Slice

- full streaming/session event protocol
- Tower lifecycle management beyond existing orchestration support
- bespoke runtime logic outside the orchestration service

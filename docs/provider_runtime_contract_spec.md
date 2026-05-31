# ATC Provider Runtime Contract Spec

## Status
Draft contract spec for the ATC runtime/provider refactor.

## Purpose
This document freezes the exact contract shape between:
- ATC orchestration/runtime service
- provider runtime implementations
- the `atc-provider` CLI wrapper layer

This contract is intended to reduce ambiguity before implementation begins.

## Scope
This spec covers:
- canonical shared enums and models
- Python-side interfaces
- wrapper event payload schemas
- exit code mapping expectations
- integration responsibilities

## Canonical role and transport enums

Suggested module:
- `src/atc/runtime/models.py`

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RoleKind(StrEnum):
    TOWER = "tower"
    LEADER = "leader"
    ACE = "ace"


class RuntimeTransport(StrEnum):
    TMUX = "tmux"


class ReadinessState(StrEnum):
    STARTING = "starting"
    READY = "ready"
    BUSY = "busy"
    BLOCKED = "blocked"
    ERROR = "error"
    STOPPED = "stopped"


class RuntimeBlockReason(StrEnum):
    LOGIN = "login"
    TRUST = "trust"
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    PROVIDER_PROMPT = "provider_prompt"
    UNKNOWN = "unknown"
```

## Shared Python models

```python
@dataclass(slots=True)
class RuntimeSessionHandle:
    session_id: str
    provider_name: str
    role: RoleKind
    transport: RuntimeTransport
    project_id: str | None = None
    tmux_session: str | None = None
    tmux_pane: str | None = None
    working_dir: str | None = None
    context_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StartRoleRequest:
    session_id: str
    provider_name: str
    role: RoleKind
    project_id: str | None = None
    working_dir: str | None = None
    context_ref: str | None = None
    display_name: str | None = None
    provider_config_ref: str | None = None
    bootstrap_file: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StopRoleRequest:
    reason: str | None = None
    graceful: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class InstructionRequest:
    session_id: str
    message: str | None = None
    message_file: str | None = None
    context_ref: str | None = None
    instruction_id: str | None = None
    expects_readiness_check: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TaskAssignmentRequest:
    session_id: str
    task_id: str
    task_title: str | None = None
    message: str | None = None
    message_file: str | None = None
    context_ref: str | None = None
    assignment_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ReadinessResult:
    session_id: str
    provider_name: str
    state: ReadinessState
    block_reason: RuntimeBlockReason | None = None
    summary: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeInspection:
    session_id: str
    provider_name: str
    alive: bool
    readiness: ReadinessState
    block_reason: RuntimeBlockReason | None = None
    summary: str | None = None
    last_output_excerpt: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
```

## Provider runtime Python interface

Suggested module:
- `src/atc/providers/base.py`

```python
from typing import Protocol


class ProviderRuntime(Protocol):
    provider_name: str

    async def prepare_workspace(self, request: StartRoleRequest) -> None: ...

    async def start_role(self, request: StartRoleRequest) -> RuntimeSessionHandle: ...

    async def stop_role(
        self,
        handle: RuntimeSessionHandle,
        request: StopRoleRequest | None = None,
    ) -> None: ...

    async def send_instruction(
        self,
        handle: RuntimeSessionHandle,
        request: InstructionRequest,
    ) -> None: ...

    async def assign_task(
        self,
        handle: RuntimeSessionHandle,
        request: TaskAssignmentRequest,
    ) -> None: ...

    async def check_readiness(
        self,
        handle: RuntimeSessionHandle,
    ) -> ReadinessResult: ...

    async def inspect_session(
        self,
        handle: RuntimeSessionHandle,
    ) -> RuntimeInspection: ...

    async def restore_session(
        self,
        handle: RuntimeSessionHandle,
    ) -> RuntimeInspection: ...
```

## Runtime service Python interface

Suggested module:
- `src/atc/runtime/service.py`

```python
class RuntimeService:
    async def start_tower(self, request: StartRoleRequest) -> RuntimeSessionHandle: ...
    async def stop_tower(self, handle: RuntimeSessionHandle, request: StopRoleRequest | None = None) -> None: ...

    async def start_leader(self, request: StartRoleRequest) -> RuntimeSessionHandle: ...
    async def stop_leader(self, handle: RuntimeSessionHandle, request: StopRoleRequest | None = None) -> None: ...

    async def start_ace(self, request: StartRoleRequest) -> RuntimeSessionHandle: ...
    async def stop_ace(self, handle: RuntimeSessionHandle, request: StopRoleRequest | None = None) -> None: ...

    async def send_instruction(self, handle: RuntimeSessionHandle, request: InstructionRequest) -> None: ...
    async def assign_project_to_leader(self, handle: RuntimeSessionHandle, request: InstructionRequest) -> None: ...
    async def assign_task_to_ace(self, handle: RuntimeSessionHandle, request: TaskAssignmentRequest) -> None: ...

    async def check_readiness(self, handle: RuntimeSessionHandle) -> ReadinessResult: ...
    async def inspect_session(self, handle: RuntimeSessionHandle) -> RuntimeInspection: ...
    async def restore_session(self, handle: RuntimeSessionHandle) -> RuntimeInspection: ...
```

## Wrapper event protocol

Suggested parser home:
- `src/atc/runtime/wrapper_events.py`

### Line format
Every machine-readable wrapper line must be:
```text
ATC_EVENT <event_name> <json_payload>
```

### Required common payload fields
All event payloads must include:
- `session_id: str`
- `provider: str`
- `command: str`

Optional common fields when relevant:
- `role: "tower" | "leader" | "ace"`
- `project_id: str`
- `task_id: str`
- `instruction_id: str`
- `assignment_id: str`
- `reason: str`
- `message: str`
- `details: object`

### Event schema definitions

#### `runtime_starting`
```json
{
  "session_id": "sess_123",
  "provider": "codex",
  "command": "start-role",
  "role": "ace",
  "project_id": "proj_456"
}
```

#### `runtime_ready`
```json
{
  "session_id": "sess_123",
  "provider": "codex",
  "command": "start-role"
}
```

#### `runtime_blocked`
```json
{
  "session_id": "sess_123",
  "provider": "claude_code",
  "command": "start-role",
  "reason": "login",
  "message": "Provider requires login"
}
```

#### `runtime_error`
```json
{
  "session_id": "sess_123",
  "provider": "codex",
  "command": "start-role",
  "message": "Provider exited early",
  "details": {"exit_code": 1}
}
```

#### `delivery_started`
```json
{
  "session_id": "sess_123",
  "provider": "codex",
  "command": "send-instruction",
  "instruction_id": "inst_123"
}
```

#### `delivery_confirmed`
```json
{
  "session_id": "sess_123",
  "provider": "codex",
  "command": "send-instruction",
  "instruction_id": "inst_123"
}
```

#### `delivery_blocked`
```json
{
  "session_id": "sess_123",
  "provider": "claude_code",
  "command": "send-instruction",
  "instruction_id": "inst_123",
  "reason": "trust",
  "message": "Trust prompt is blocking input"
}
```

#### `delivery_error`
```json
{
  "session_id": "sess_123",
  "provider": "codex",
  "command": "send-instruction",
  "instruction_id": "inst_123",
  "message": "Delivery confirmation timed out"
}
```

#### `task_assignment_started`
```json
{
  "session_id": "sess_ace_1",
  "provider": "codex",
  "command": "assign-task",
  "task_id": "task_123",
  "assignment_id": "assign_456"
}
```

#### `task_assignment_confirmed`
```json
{
  "session_id": "sess_ace_1",
  "provider": "codex",
  "command": "assign-task",
  "task_id": "task_123",
  "assignment_id": "assign_456"
}
```

#### `task_assignment_blocked`
```json
{
  "session_id": "sess_ace_1",
  "provider": "codex",
  "command": "assign-task",
  "task_id": "task_123",
  "assignment_id": "assign_456",
  "reason": "provider_prompt",
  "message": "Interactive provider prompt blocked assignment"
}
```

#### `task_assignment_error`
```json
{
  "session_id": "sess_ace_1",
  "provider": "codex",
  "command": "assign-task",
  "task_id": "task_123",
  "assignment_id": "assign_456",
  "message": "Submission failed"
}
```

#### `readiness_result`
```json
{
  "session_id": "sess_123",
  "provider": "codex",
  "command": "check-readiness",
  "state": "ready",
  "reason": null,
  "message": "Prompt ready"
}
```

#### `inspection_result`
```json
{
  "session_id": "sess_123",
  "provider": "codex",
  "command": "inspect-session",
  "alive": true,
  "state": "busy",
  "reason": null,
  "message": "Processing prior instruction",
  "details": {"last_output_excerpt": "Working..."}
}
```

#### `restore_result`
```json
{
  "session_id": "sess_123",
  "provider": "codex",
  "command": "restore-session",
  "alive": true,
  "state": "ready",
  "reason": null,
  "message": "Restored existing tmux-backed provider session"
}
```

## Wrapper exit code mapping

```python
class WrapperExitCode(IntEnum):
    SUCCESS = 0
    BLOCKED_AUTH = 10
    NOT_READY = 11
    SESSION_MISSING = 12
    INVALID_ARGS = 13
    DELIVERY_FAILED = 20
    RESTORE_FAILED = 21
    INTERNAL_FAILURE = 30
```

Runtime-service interpretation expectations:
- `0`: operation succeeded
- `10`: session/provider blocked by auth/trust/login class issue
- `11`: transient not-ready or busy state
- `12`: target runtime session/pane missing or dead
- `13`: programming/config error in invocation
- `20`: delivery/assignment failure
- `21`: restore failure
- `30`: unexpected wrapper failure

## Responsibility rules

### Orchestration/runtime service must
- resolve provider implementation from config/session metadata
- construct the wrapper invocation
- persist session/runtime metadata
- parse wrapper markers and exit codes
- translate normalized results into ATC session/orchestration state

### Orchestration/runtime service must not
- hardcode provider command strings
- implement provider-specific startup sleeps
- implement provider-specific delivery timing hacks
- parse provider-specific terminal quirks outside documented wrapper/provider logic

### Provider runtime implementation must
- own provider command construction
- own provider readiness interpretation
- own provider-specific instruction/task delivery mechanics
- own wrapper invocation behavior for that provider

## Compatibility rules
- New providers are not first-class until they implement the shared contract.
- Compatibility shims are allowed temporarily, but must be documented and scheduled for removal.
- Shared orchestration code should never branch on provider behavior once a provider implements the contract for that operation.

## Recommended next step
After agreeing on this spec, write the first implementation checklist as Phase 0:
- create `runtime/models.py`
- create `providers/base.py`
- create wrapper event parser models
- create wrapper exit code enum
- define `manager -> leader` compatibility notes
- define initial provider registry shape

## Provider-stamped session rule
Every ATC session row must be treated as provider-stamped at birth.

Meaning:
- `sessions.provider` is not just a hint
- it is the runtime identity of the session row
- restore/reconnect/spawn-existing flows must respect it

## Provider mismatch rule
Before any `spawn_existing_session(...)` or restore/reconnect path materializes a session row into a live runtime session, the runtime must compare:
- the provider stamped on the session row
- the provider currently desired by the higher-level role/scope/config path

### If the providers match
- the runtime may restore/reuse/materialize that session row

### If the providers do not match
- the runtime must not silently reuse the old session
- the runtime must treat the old row as incompatible with the current desired provider
- the correct behavior is replacement, not mutation-in-place

Recommended outcomes on mismatch:
- mark old session as disconnected/superseded/stale
- create a fresh session row under the new provider when the higher-level workflow wants a new live session
- do not mutate the old session row's provider field to "convert" it

## Semantic split between startup operations
This refactor should distinguish between two different operations:

### Role startup
Examples:
- `start_tower(...)`
- `start_leader(...)`
- `start_ace(...)`

Meaning:
- start a new role session under the currently desired provider semantics
- orchestration-facing operation

### Existing-session materialization
Example:
- `spawn_existing_session(...)`

Meaning:
- materialize an already-created DB session row into a live tmux/provider runtime session
- runtime-facing operation
- valid only when the stamped provider identity on the row is still compatible with the intended provider path

This is why `spawn_existing_session(...)` should never mean "blindly resurrect whatever old row exists".

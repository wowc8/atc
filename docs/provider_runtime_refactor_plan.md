# ATC provider runtime refactor plan

## Executive summary
ATC should converge on one central tmux-based provider runtime interface. The orchestration/backend layers should express role-based operations such as start Tower, stop Leader, assign task to Ace, send instruction, check readiness, and restore sessions without caring which provider implementation is active. Provider-specific logic should live only in provider-owned modules.

This refactor should not remove tmux. tmux is the common runtime transport and should be treated as a stable substrate. The refactor should move provider-specific CLI semantics behind a single contract, not attempt to abstract away terminal execution itself.

## Design goals
- Keep tmux as the common transport/runtime substrate.
- Make the backend provider-neutral, not transport-neutral.
- Make one runtime service the only legal entry point for provider-specific behavior.
- Make Tower, Leader, and Ace all use the same runtime contract.
- Normalize role nouns and session metadata so refactors stop fighting naming drift.
- Get Codex support by implementing the contract cleanly, not by adding more special cases.

## What should stay shared vs provider-specific

### Shared across all providers
These are tmux/runtime concerns and should live in shared infrastructure:
- ensure tmux session exists
- spawn pane/window/session
- kill pane/window/session
- capture pane text
- send raw keys/paste/input
- resize pane
- attach/detach PTY stream
- check pane/session existence
- persist tmux identifiers and generic runtime metadata

### Provider-specific
These should be fully encapsulated in provider modules:
- launch command construction
- startup handshake/trust/login handling
- readiness detection
- prompt/instruction framing
- output parsing hints
- workspace/config/bootstrap file generation
- recovery heuristics
- provider-specific health or status signals

## Proposed contract shape

### Shared enums/models
Create canonical shared models first.

Suggested file:
- `src/atc/runtime/models.py`

Suggested core enums:
```python
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
```

Suggested session handle:
```python
@dataclass(slots=True)
class RuntimeSessionHandle:
    session_id: str
    project_id: str | None
    role: RoleKind
    provider_name: str
    transport: RuntimeTransport
    tmux_session: str | None
    tmux_pane: str | None
    metadata: dict[str, Any]
```

Suggested requests/responses:
```python
@dataclass(slots=True)
class StartRoleRequest:
    session_id: str
    project_id: str | None
    role: RoleKind
    working_dir: str | None
    display_name: str | None
    bootstrap_context: dict[str, Any]

@dataclass(slots=True)
class StopRoleRequest:
    reason: str | None = None

@dataclass(slots=True)
class InstructionRequest:
    text: str
    expects_prompt_disappearance: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class TaskAssignmentRequest:
    task_id: str
    title: str
    brief: str
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class ReadinessResult:
    state: ReadinessState
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class RuntimeInspection:
    alive: bool
    readiness: ReadinessState
    last_output_excerpt: str | None
    metadata: dict[str, Any] = field(default_factory=dict)
```

### Central provider runtime interface
Suggested file:
- `src/atc/providers/base.py`

Suggested interface:
```python
class ProviderRuntime(Protocol):
    provider_name: str

    async def prepare_workspace(self, role: RoleKind, request: StartRoleRequest) -> None: ...
    async def start_role(self, request: StartRoleRequest) -> RuntimeSessionHandle: ...
    async def stop_role(self, handle: RuntimeSessionHandle, request: StopRoleRequest | None = None) -> None: ...
    async def send_instruction(self, handle: RuntimeSessionHandle, request: InstructionRequest) -> None: ...
    async def assign_task(self, handle: RuntimeSessionHandle, request: TaskAssignmentRequest) -> None: ...
    async def check_readiness(self, handle: RuntimeSessionHandle) -> ReadinessResult: ...
    async def inspect_session(self, handle: RuntimeSessionHandle) -> RuntimeInspection: ...
    async def restore_session(self, handle: RuntimeSessionHandle) -> RuntimeInspection: ...
```

Notes:
- `assign_task()` can be a semantic alias over `send_instruction()`, but it is useful to keep as a first-class orchestration call.
- `start_role()` should be the provider-owned entry point for Tower, Leader, and Ace startup.
- shared orchestration code should not build provider-specific prompts or startup commands directly.

### Central runtime service
Suggested file:
- `src/atc/runtime/service.py`

Responsibilities:
- resolve configured provider
- build `StartRoleRequest` / `InstructionRequest` / `TaskAssignmentRequest`
- load/persist `RuntimeSessionHandle`
- call the selected provider runtime implementation
- expose one API to higher layers

Suggested interface:
```python
class RuntimeService:
    async def start_tower(self, ...) -> RuntimeSessionHandle: ...
    async def stop_tower(self, ...) -> None: ...
    async def start_leader(self, ...) -> RuntimeSessionHandle: ...
    async def stop_leader(self, ...) -> None: ...
    async def start_ace(self, ...) -> RuntimeSessionHandle: ...
    async def stop_ace(self, ...) -> None: ...
    async def assign_project_to_leader(self, ...) -> None: ...
    async def assign_task_to_ace(self, ...) -> None: ...
    async def send_instruction(self, ...) -> None: ...
    async def inspect_session(self, session_id: str) -> RuntimeInspection: ...
    async def restore_session(self, session_id: str) -> RuntimeInspection: ...
```

This becomes the only legal entry point from routers/controllers/orchestrators.

## Proposed folder structure to adopt first

```text
src/atc/
  runtime/
    __init__.py
    models.py
    errors.py
    service.py
    tmux/
      __init__.py
      substrate.py
      capture.py
      control.py
      streaming.py
      health.py

  providers/
    __init__.py
    base.py
    registry.py
    claude_code/
      __init__.py
      runtime.py
      workspace.py
      readiness.py
      prompts.py
      parser.py
    codex/
      __init__.py
      runtime.py
      workspace.py
      readiness.py
      prompts.py
      parser.py
    opencode/
      __init__.py
      runtime.py
      workspace.py
      readiness.py
      prompts.py
      parser.py
```

## Mapping from current files to future ownership

### Keep, but relocate or slim down
- `src/atc/terminal/control.py`
  - future home: `src/atc/runtime/tmux/control.py`
- `src/atc/terminal/pty_stream.py`
  - future home: `src/atc/runtime/tmux/streaming.py`
- tmux spawn/capture helpers currently in `src/atc/session/ace.py`
  - future home: `src/atc/runtime/tmux/substrate.py`
- provider interface bits from `src/atc/agents/base.py`
  - evolve into `src/atc/providers/base.py`
- provider registry bits from `src/atc/agents/factory.py`
  - evolve into `src/atc/providers/registry.py`

### Shrink or replace
- `src/atc/session/ace.py`
  - should stop owning generic spawn/readiness/provider logic
  - should become thin orchestration helpers or disappear into `runtime/service.py`
- `src/atc/leader/leader.py`
  - should stop doing provider-specific startup directly
  - should call `RuntimeService.start_leader()`
- `src/atc/tower/session.py`
  - should stop doing provider-specific startup directly
  - should call `RuntimeService.start_tower()`
- `src/atc/session/reconnect.py`
  - should become runtime-service driven restore logic

### Higher-level callers that should become provider-neutral
- `src/atc/tower/controller.py`
- `src/atc/leader/orchestrator.py`
- `src/atc/api/routers/aces.py`
- `src/atc/api/routers/leader.py`
- `src/atc/api/routers/projects.py`

## Explicit implementation phases

## Phase 0, normalize nouns and freeze the contract
Goal: define the refactor target before moving code.

Target files/modules:
- new: `src/atc/runtime/models.py`
- new: `src/atc/providers/base.py`
- new: `src/atc/providers/registry.py`
- update: `src/atc/state/models.py`
- update: `frontend/src/types/index.ts`
- update: `docs/codebase_map_current_state.md`
- update: `docs/provider_runtime_refactor_plan.md`

Tasks:
- define canonical `RoleKind`, `RuntimeSessionHandle`, request/response models
- decide how `manager -> leader` migration will be represented in code and DB
- define the runtime service API in code and docs
- document temporary compatibility shims

Exit criteria:
- one agreed role vocabulary
- one agreed provider runtime interface
- one agreed runtime service surface

## Phase 1, extract shared tmux substrate
Goal: make tmux support clean and reusable.

Target files/modules:
- new: `src/atc/runtime/tmux/substrate.py`
- new: `src/atc/runtime/tmux/control.py`
- new: `src/atc/runtime/tmux/capture.py`
- new: `src/atc/runtime/tmux/streaming.py`
- update/migrate from:
  - `src/atc/session/ace.py`
  - `src/atc/terminal/control.py`
  - `src/atc/terminal/pty_stream.py`

Tasks:
- move generic tmux helpers into shared substrate
- keep substrate provider-agnostic
- expose stable helpers for spawn, kill, capture, send, resize, attach stream, and health checks

Exit criteria:
- provider/runtime modules no longer need to reach into role-specific code for tmux basics

## Phase 2, create provider runtime implementations over the tmux substrate
Goal: make provider modules the real owners of provider behavior.

Target files/modules:
- new: `src/atc/providers/claude_code/runtime.py`
- new: `src/atc/providers/claude_code/readiness.py`
- new: `src/atc/providers/claude_code/workspace.py`
- new: `src/atc/providers/codex/runtime.py`
- new: `src/atc/providers/codex/readiness.py`
- new: `src/atc/providers/codex/workspace.py`
- optional/parallel: `src/atc/providers/opencode/*`
- update/migrate from:
  - `src/atc/agents/claude_provider.py`
  - `src/atc/agents/opencode_provider.py`
  - `src/atc/agents/deploy.py`
  - `src/atc/agents/auth.py`

Tasks:
- make each provider implement `ProviderRuntime`
- move command construction and readiness logic out of shared session code
- move provider-specific workspace/config generation into provider packages
- implement Codex as a first-class provider runtime, not a patched launch-command variant

Exit criteria:
- provider behavior is encapsulated in provider directories
- no shared lifecycle code contains provider-specific startup/readiness logic

## Phase 3, introduce RuntimeService and move all role startup to it
Goal: make one service the lifecycle choke point.

Target files/modules:
- new: `src/atc/runtime/service.py`
- update:
  - `src/atc/tower/session.py`
  - `src/atc/leader/leader.py`
  - `src/atc/session/ace.py`
  - `src/atc/api/app.py`

Tasks:
- implement `start_tower`, `start_leader`, `start_ace`
- implement `stop_tower`, `stop_leader`, `stop_ace`
- persist runtime metadata consistently
- make callers stop invoking provider logic directly

Exit criteria:
- all role session startup/stop goes through `RuntimeService`

## Phase 4, move instruction, assignment, readiness, and inspection flows behind the runtime service
Goal: make execution flows provider-neutral too.

Target files/modules:
- update:
  - `src/atc/tower/controller.py`
  - `src/atc/leader/orchestrator.py`
  - `src/atc/api/routers/leader.py`
  - `src/atc/api/routers/aces.py`
  - `src/atc/session/state_machine.py`

Tasks:
- route leader kickoff through `assign_project_to_leader()` / `send_instruction()`
- route ace task assignment through `assign_task_to_ace()`
- route readiness checks through provider runtime implementations
- centralize inspection/status snapshot logic

Exit criteria:
- orchestration code expresses workflow intent only
- provider/CLI mechanics are hidden behind runtime service calls

## Phase 5, unify restore/reconnect under provider-aware runtime restoration
Goal: eliminate split restore ownership.

Target files/modules:
- update:
  - `src/atc/session/reconnect.py`
  - `src/atc/api/app.py`
  - `src/atc/tower/controller.py`
  - `src/atc/state/db.py`

Tasks:
- make `restore_session()` provider-aware
- stop having separate ad hoc Tower restore semantics where possible
- persist enough runtime metadata for clean restart behavior
- ensure Tower, Leader, and Ace all restore through the same conceptual path

Exit criteria:
- restore/reconnect policy is centralized
- session resurrection no longer depends on scattered role-specific code paths

## Phase 6, clean up model drift, docs, and tests
Goal: finish the refactor honestly.

Target files/modules:
- update:
  - `src/atc/state/models.py`
  - `src/atc/state/db.py`
  - `src/atc/api/routers/projects.py`
  - `src/atc/api/routers/tasks.py`
  - `frontend/src/context/AppContext.tsx`
  - `frontend/src/types/index.ts`
  - `docs/API.md`
  - `docs/ARCHITECTURE.md`
  - `docs/PATTERNS.md`
  - relevant tests in `tests/unit`, `tests/integration`, `tests/e2e`, and `frontend/tests`

Tasks:
- finish `manager -> leader` cleanup
- decide and clean up `tasks` vs `task_graphs`
- resync API/WebSocket/docs to runtime truth
- remove dead compatibility shims when safe
- add contract and integration coverage around runtime service/provider implementations

Exit criteria:
- docs match code
- tests defend the new boundary
- stale model drift is materially reduced

## First slice I would implement
If starting immediately, I would do this exact order:
1. create `src/atc/runtime/models.py`
2. create `src/atc/providers/base.py`
3. create `src/atc/runtime/tmux/substrate.py`
4. migrate low-level tmux helpers there
5. create `src/atc/providers/claude_code/runtime.py`
6. create `src/atc/providers/codex/runtime.py`
7. create `src/atc/runtime/service.py`
8. switch Tower startup to call `RuntimeService.start_tower()` first
9. switch Leader startup next
10. switch Ace startup last

Why this order:
- it creates the boundary before broad code motion
- it proves the contract on one role first
- it gives Codex a clean home immediately
- it reduces the temptation to keep patching `get_launch_command()` style flows

## Final recommendation
The first folder structure to adopt should be `runtime/` plus `providers/`, with tmux substrate code explicitly shared under `runtime/tmux/` and provider-specific logic fully isolated under `providers/<name>/`.

That is the cleanest path to the architecture Matthew wants: tmux-native, provider-neutral in orchestration, and implementation-specific only inside provider modules.

## Live milestone update

### 2026-05-31 Phase 0 progress now partially live
The refactor is no longer only planned scaffolding.

The following are now real and landed in code:
- shared runtime contract models under `src/atc/runtime/models.py`
- provider runtime protocol under `src/atc/providers/base.py`
- wrapper event parsing under `src/atc/runtime/wrapper_events.py`
- wrapper/runtime error layer under `src/atc/runtime/errors.py`
- shared tmux substrate beginnings under `src/atc/runtime/tmux/`
- built-in new provider runtimes for Claude and Codex under `src/atc/providers/claude_code/` and `src/atc/providers/codex/`
- built-in runtime registry defaults for `claude_code` and `codex`
- first real instruction-delivery path routed through `RuntimeService`
- first real startup spawn path routed through `RuntimeService` via `_spawn_provider_session()`

This means the refactor has crossed from design-only into partial live replacement. Future work should treat the new runtime/provider boundary as active infrastructure, not speculative scaffolding.

## MCP, orchestration, runtime, wrappers, and hooks stack

The intended layering is:

1. **MCP / external control surface**
   - high-level tool/API surface for ATC control
   - should call orchestration services, not provider-specific runtime code directly

2. **ATC orchestration/application services**
   - decides what should run, when, and why
   - owns project/task/assignment policy and workflow state transitions

3. **ATC runtime service**
   - provider-neutral runtime boundary
   - resolves provider implementation and executes lifecycle/delivery operations

4. **Provider runtime implementations**
   - provider-specific behavior for Claude/Codex/OpenCode
   - own readiness, startup quirks, delivery mechanics, and restore semantics

5. **ATC provider CLI wrapper + hooks**
   - terminal-facing provider adapter behavior
   - wrapper commands, startup timing, trust/login handling, hook entrypoints

6. **Shared tmux substrate**
   - pane/session spawn, capture, resize, send input, stream output, health checks

### Layering rule
- MCP belongs above orchestration.
- Runtime/provider refactor belongs below orchestration.
- Hooks belong in the provider/runtime execution layer, not in orchestration policy.

### Practical rule
When MCP-triggered work starts or controls a session, the call path should eventually flow through:
- orchestration service
- runtime service
- provider runtime
- wrapper/hooks/tmux substrate

It should not bypass directly to provider-specific logic from the MCP layer.

## Provider mismatch and replace-not-mutate rule

Sessions are provider-stamped at birth.
The `sessions.provider` value is the runtime identity of that session row.

When the currently desired provider changes:
- old sessions from the previous provider must not be silently reused
- old session rows must not be mutated in place to pretend they were born under the new provider
- provider mismatch should lead to replacement behavior, not session identity mutation

This rule applies especially to:
- restore/reconnect
- `spawn_existing_session(...)`
- any session reuse path

Practical rule:
- match => reuse/materialize may be allowed
- mismatch => replace, do not mutate in place


## Live reconnect seam status
The reconnect/respawn seam now follows the stamped-provider rule before runtime materialization:
- `src/atc/session/reconnect.py` compares `session.provider` to the current desired provider
- mismatch marks the old session disconnected and refuses reuse
- match allows respawn/materialization to continue through `_spawn_provider_session(...)`
- `_spawn_provider_session(...)` now routes existing-row materialization through `RuntimeService.spawn_existing_session(...)`

This means reconnect/reuse is no longer just a documented policy. It is now part of the live seam behavior.


## Live Tower seam status
The Tower startup/reuse seam no longer depends on a direct `get_launch_command(...)` call in `src/atc/tower/session.py`.
Tower startup still performs Tower-specific file deployment and reuse checks, but runtime materialization now continues through `_spawn_provider_session(...)` without that extra direct factory dependency.


## Live Leader orchestration seam status
The Ace spawn path in `src/atc/leader/orchestrator.py` no longer resolves a direct launch command before calling `create_ace(...)`.
That path now relies on the DB-stamped/session-driven provider flow inside session creation and existing-session materialization rather than passing an extra launch-command decision down from the orchestrator layer.


## Live Ace API seam status
The Ace creation REST path in `src/atc/api/routers/aces.py` no longer resolves a direct `get_launch_command(...)` value before calling `create_ace(...)`.
The router now stays closer to orchestration intent, while provider/runtime materialization details continue lower in the session/runtime layers.


## Live settings/provider discovery seam status
The provider-listing path in `src/atc/api/routers/settings.py` no longer instantiates old agent-factory providers to read capabilities.
It now reads provider discovery metadata from the runtime registry boundary instead.

This is a different seam category than session startup/materialization, but it removes another visible old provider-factory dependency from live code.


## Live runtime hardening milestone: inspection/readiness/restore
Claude and Codex runtimes now have a stronger first-pass inspection model:
- missing pane => `stopped`
- visible bare prompt => `ready`
- live pane without visible prompt => `busy`
- `check_readiness()` now derives from `inspect_session()`
- `restore_session()` now reflects the richer inspection result instead of treating pane existence alone as sufficient

This is still an early hardening pass, but it is the first real step where the runtime layer carries meaningful restore/readiness behavior instead of only structural placeholders.


## Live runtime hardening milestone: blocked-state detection
The first blocked-state detection pass is now live in provider runtime inspection:
- Claude runtime can distinguish trust-style prompts from auth-style prompts
- Codex runtime can distinguish auth-style prompts
- blocked states now surface as `ReadinessState.BLOCKED` with `RuntimeBlockReason`

This is still intentionally heuristic, but it is a meaningful step toward making runtime restore/readiness semantics represent usability rather than mere process existence.


## Live lifecycle regression coverage milestone
Lifecycle/provider-mismatch behavior now has stronger regression coverage:
- Tower reuse tests cover same-provider reuse versus provider-mismatch replacement
- reconnect tests cover provider-mismatch refusal versus same-provider respawn

This helps lock the newer provider-stamped session semantics into test-guarded behavior instead of leaving them as implementation intent only.


## Live Tower restore consistency status
The startup restore path in `src/atc/api/app.py` now enforces the stamped-provider rule for Tower sessions before restoring controller state.
A live Tower pane with a mismatched provider is now marked disconnected and refused for restore, matching the replace-not-reuse behavior already established in reconnect and Tower startup-reuse paths.


## Live Leader reuse consistency status
The Leader startup path in `src/atc/leader/leader.py` now enforces the stamped-provider rule before reusing an existing linked leader session.
If the existing leader session was born under a different provider than the current desired provider, reuse is refused, the old session is marked disconnected, and the leader row is cleared so a replacement session can be created.


## Live runtime hardening milestone: restore semantics
Provider `restore_session(...)` now adds first-pass restore-specific meaning on top of inspection results.
Restore results now explicitly communicate whether a session appears usable after restore, whether it needs attention, and whether the outcome was ready, still active, blocked, or failed due to a missing pane.

## What's left checklist

### A. Remaining mixed old/new architecture seams
- [ ] audit remaining `atc.agents.factory` usage outside the new runtime boundary
- [ ] decide whether `atc/orchestration/service.py` launch-command path should move to runtime-owned spawn/materialization semantics
- [ ] sweep for any lingering provider-specific behavior living in orchestration/session layers instead of provider runtimes
- [ ] remove or clearly mark transitional compatibility scaffolding that is no longer intended as the primary path

### B. Lifecycle semantic consistency
- [ ] sweep all remaining reuse/restart/restore/materialization paths for stamped-provider enforcement
- [ ] confirm every path follows: provider match => may reuse, provider mismatch => replace/do not mutate/reuse
- [ ] add any missing regression coverage for remaining lifecycle seams after the audit sweep completes

### C. Runtime/provider behavior hardening
- [ ] deepen `restore_session(...)` beyond first-pass pane/prompt heuristics where provider-specific behavior is known
- [ ] improve readiness and blocked-state detection beyond current text heuristics where feasible
- [ ] add clearer provider-specific semantics for auth/login/trust/provider-prompt states
- [ ] make `spawn_existing_session(...)` and `restore_session(...)` behaviors more intentionally distinct when needed

### D. Wrapper and hook contract hardening
- [ ] document and harden wrapper event semantics beyond the current first-pass contract
- [ ] ensure hook ownership stays in provider/runtime layer, not orchestration policy
- [ ] reduce hidden higher-layer assumptions about provider terminal behavior

### E. Testing and confidence
- [ ] broaden lifecycle/integration confidence beyond the current targeted slices
- [ ] add stronger reconnect/restore/provider-switch coverage where still thin
- [ ] add tests for the remaining runtime/provider hardening behavior once semantics stabilize

### F. Final reconciliation and cleanup
- [ ] reconcile `provider_runtime_refactor_plan.md` checklist/status against final landed code reality
- [ ] refresh any docs that still imply older flows are primary
- [ ] remove dead or redundant old helper paths once the new boundary is clearly dominant
- [ ] decide the explicit finish line for calling the refactor complete

## Recommended next checklist item
- [ ] next: inspect `src/atc/orchestration/service.py` and decide whether its remaining `get_launch_command(...)` path should move onto the new runtime boundary or remain intentionally separate for now


## Live orchestration Ace-spawn seam status
The `spawn_ace(...)` path in `src/atc/orchestration/service.py` no longer resolves a direct `get_launch_command(...)` value before calling `create_ace(...)`.
That orchestration-layer path now stays closer to intent while provider-backed materialization continues lower in the session/runtime boundary, matching the cleanup already done in the REST and leader orchestration Ace spawn seams.


## Live runtime hardening milestone: provider-specific restore hints
Restore results now include first-pass provider-specific restore hints in `details`, including restore stage and suggested action.
Examples include Claude warm-up/trust/auth distinctions and Codex auth-gate versus active/ready distinctions.
This is still heuristic, but it makes restore behavior more provider-aware than the earlier generic wrapper around inspection states.


## Live runtime hardening milestone: provider-specific inspection hints
Coverage added for reconnect mismatch without a project anchor and for tower provider-mismatch reuse forcing the old tower row to `disconnected`, so provider-stamped replacement behavior is now asserted more explicitly in the lifecycle tests.
Provider inspection results now expose first-pass provider-specific runtime hints and suggested actions in `details`, not only at restore time.
This keeps shared layers normalized while giving downstream generic callers richer provider-owned signals behind the provider runtime boundary.


Settings and project provider validation now list provider names from the runtime registry rather than the older agent factory, reducing one more mixed old/new seam on the live API surface.
The runtime registry now also exposes non-empty built-in provider metadata for the live `/settings/providers` surface, so frontend provider lists do not depend on placeholder-zero metadata rows.

File migration replay is now guarded so `015_session_provider_scope.sql` is recorded and skipped when fresh-schema databases already contain the `sessions.provider` column, preventing startup failure from duplicate-column reapplication on new installs.

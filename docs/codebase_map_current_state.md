# ATC current-state architecture brief

## Executive summary
ATC already has the beginnings of a provider abstraction, but the current runtime is still only partially organized around it. The real execution core remains tmux-centric and strongly shaped by Claude-specific startup, readiness, and terminal assumptions. That means the product surface says providers are selectable, while the backend often still behaves as though it is operating one default provider with alternate launch commands.

The right refactor direction is not to remove tmux. It is to make tmux the stable transport substrate and move all provider-specific CLI behavior behind one central runtime interface. The backend should orchestrate roles and workflows without caring whether the underlying provider implementation is Claude Code, Codex, OpenCode, or another tmux-driven AI CLI.

## What exists today

### Durable strengths
- ATC already has a clear role model: Tower, Leader, Ace.
- tmux is already the common runtime transport for interactive CLI sessions.
- SQLite gives the system durable state for projects, sessions, task graphs, assignments, context, and monitoring.
- FastAPI, websocket, PTY streaming, and the event bus provide a workable orchestration shell.
- There is already an `AgentProvider` abstraction in `src/atc/agents/base.py` and a registry in `src/atc/agents/factory.py`.

### What is not actually clean yet
- Provider selection often changes a launch command, not the lifecycle/control boundary.
- Shared session code still contains Claude-specific readiness, trust-dialog, workspace, and prompt assumptions.
- Session creation, restart, and reconnect paths are split across multiple layers instead of going through one runtime boundary.
- Tower, Leader, and Ace do not all go through one unified provider/runtime contract.
- Role terminology and task modeling have drifted enough to make refactoring riskier than it should be.

## Current architecture by layer

### 1. Product/orchestration layer
This is the part of ATC that should remain provider-agnostic.

Primary responsibilities:
- manage projects
- manage Tower, Leader, and Ace roles
- submit goals
- decompose work
- assign tasks
- track progress
- enforce budgets/concurrency
- persist state
- restore operational context

Important files today:
- `src/atc/tower/controller.py`
- `src/atc/leader/orchestrator.py`
- `src/atc/api/routers/projects.py`
- `src/atc/api/routers/leader.py`
- `src/atc/api/routers/aces.py`
- `src/atc/state/db.py`

Reality today: this layer still knows too much about provider-specific runtime behavior.

### 2. Runtime/session-control layer
This is where tmux-backed terminal lifecycle is actually controlled.

Important files today:
- `src/atc/session/ace.py`
- `src/atc/leader/leader.py`
- `src/atc/tower/session.py`
- `src/atc/session/reconnect.py`
- `src/atc/terminal/control.py`
- `src/atc/terminal/pty_stream.py`

Reality today:
- this layer is the de facto operational core
- it is shared across roles, but not cleanly abstracted behind one interface
- it leaks provider assumptions upward

### 3. Provider layer
This should be the only place where CLI-specific behavior lives.

Important files today:
- `src/atc/agents/base.py`
- `src/atc/agents/factory.py`
- `src/atc/agents/claude_provider.py`
- `src/atc/agents/opencode_provider.py`

Reality today:
- the abstraction exists
- but much of the runtime bypasses it and uses `get_launch_command()` plus shared tmux helpers directly
- providers are not the true boundary yet

### 4. UI layer
The frontend is already mostly expressing the right high-level intent, but it is coupled to backend drift.

Important files today:
- `frontend/src/context/AppContext.tsx`
- `frontend/src/pages/ProjectView.tsx`
- `frontend/src/components/tower/*`
- `frontend/src/components/leader/*`
- `frontend/src/components/ace/*`

Reality today:
- the UI treats providers as selectable
- but several flows still assume Claude-shaped runtime behavior or incomplete provider semantics

## What the mapping found

### A. The current provider abstraction is partial, not authoritative
ATC has an interface, but the runtime does not consistently use it as the single owner of provider behavior.

Instead, the real pattern is often:
1. choose provider
2. get launch command
3. spawn pane through shared tmux/session helpers
4. run shared readiness/delivery logic
5. special-case provider behavior later

That is why selectable providers feel unstable.

### B. tmux is already the true shared substrate
That is good news. Matthew's clarified direction matches the architecture that wants to exist here.

The stable shared substrate should be:
- tmux session/window/pane lifecycle
- PTY capture/streaming
- resize/input/output plumbing
- pane existence/aliveness checks
- restore/reconnect hooks

Those are cross-provider concerns and can remain shared.

### C. Provider-specific code should only own CLI semantics
Examples of provider-owned concerns:
- command construction
- startup expectations and prompts
- trust/login/readiness detection
- instruction framing rules
- how to determine "idle/ready/working"
- output parsing hints
- provider-specific config files or workspace scaffolding
- provider-specific recoverability rules

### D. Backend orchestration should talk in role operations, not CLI details
The backend should ask for operations such as:
- start tower
- stop tower
- start leader
- stop leader
- start ace
- stop ace
- send leader instruction
- send ace task assignment
- check readiness/status
- capture transcript/output
- reconnect/restore existing session

Those should go through one central runtime interface.

## Biggest current architectural problems

### 1. Shared code still knows Claude-shaped behavior
Examples surfaced in the mapping:
- trust-dialog handling in shared paths
- Claude-specific workspace prep leaking into generic session creation
- readiness logic tied to expected terminal patterns
- frontend auto-start logic keyed to `claude_code`

### 2. Runtime ownership is split across too many modules
The same lifecycle is partly owned by:
- role-specific session files
- reconnect logic
- provider helpers
- routers/controllers
- terminal control helpers

That makes provider encapsulation incomplete.

### 3. Terminology drift raises refactor complexity
The repo still mixes:
- product term: `Leader`
- runtime/storage/API term: `manager`

This drift is everywhere and should be normalized before or during the refactor.

### 4. Legacy and live work models coexist
- legacy `tasks`
- live `task_graphs`

That is a separate but important cleanup seam because it impacts orchestration/UI boundaries.

### 5. Docs/contracts have drifted from runtime truth
This matters because ATC is being developed with docs/design logs as active artifacts. Those need to be resynced once the new runtime boundary is agreed.

## Recommended target architecture

### Core principle
The backend should not care which AI CLI provider is being used.

It should care about:
- role: Tower, Leader, Ace
- desired operation: start, stop, instruct, assign, inspect, restore
- persisted session identity/state
- workflow outcomes

It should not care about:
- exact CLI startup command details
- provider-specific prompt quirks
- provider-specific readiness heuristics
- provider-specific login/trust sequences

### Stable layering

#### Layer 1: orchestration/application layer
Provider-agnostic.
- tower workflows
- leader workflows
- ace workflows
- task assignment/decomposition
- project state
- persistence and recovery policy

#### Layer 2: tmux runtime substrate
Shared, provider-agnostic transport.
- spawn pane/window/session
- send raw input
- capture output
- stream PTY
- resize
- check pane/session aliveness
- reconnect to existing pane

#### Layer 3: provider runtime interface
Provider-specific, tmux-backed behavior.
- construct commands
- attach provider-specific bootstrap files
- determine readiness
- send prompts/instructions correctly
- interpret output and state transitions
- detect failures/login/trust conditions

#### Layer 4: provider implementations
Examples:
- `providers/claude_code/*`
- `providers/codex/*`
- `providers/opencode/*`

Each one implements the same role/runtime contract.

## Recommendation
The direction Matthew described is the right one.

ATC should keep tmux as the common runtime transport, but the provider refactor should make one runtime interface the only legal entry point for provider-specific behavior. If that is done cleanly, Codex support stops being a special adaptation problem and becomes a normal implementation of the same contract.

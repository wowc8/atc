# ATC Code Structure

This document describes the intended shape of the ATC codebase.

It is not a line-by-line map. It is the high-level contract for how we build, extend, and keep this app healthy over time.

## What ATC Is

ATC is a hierarchical AI orchestration platform.

Core model:
- **Tower** sets goals, watches resources, and delegates at the system level
- **Leader** owns planning and coordination within a project
- **Ace** executes scoped tasks in isolated agent sessions

The app should make that hierarchy visible and reliable without hardwiring the product to one specific agent backend.

## Core Architectural Direction

### 1. Agent backends must be provider-agnostic

ATC should not be architected as "a Claude app with adapters around it".

It may have a best-supported provider at a given moment, but the architecture should assume:
- multiple agent providers can exist
- session lifecycle should not be hardcoded to one provider
- startup, readiness, prompt delivery, and status handling belong at the provider boundary when provider-specific
- product logic should depend on stable internal interfaces, not tool-specific terminal behaviors

Practical rule:
- Tower / Leader / Ace flows should talk to provider abstractions, not raw tmux or provider-specific CLI details, unless that code is explicitly part of a provider implementation

### 2. Product logic and runtime wiring should stay separate

Keep these concerns distinct:
- **product/domain logic**: Tower, Leader, Ace behavior, planning, orchestration, state transitions
- **runtime/session lifecycle**: spawning, reconnecting, readiness, prompt delivery, streaming, shutdown
- **integration details**: tmux, REST providers, CLI auth, filesystem deployment, GitHub, etc.

A clean feature usually touches one layer primarily and crosses boundaries through narrow interfaces.

### 3. State should be explicit and durable

ATC is an orchestration app. Hidden state is poison.

Prefer:
- DB-backed state over inferred in-memory state
- explicit lifecycle transitions over ad hoc status mutation
- source-of-truth records over reconstructing behavior from side effects

Current durable task graph and task assignment transitions are centralized in
`src/atc/state/transitions.py`. API-visible transition failures return stable
reason codes and allowed-target lists so Tower/Leader surfaces can display why a
workflow is blocked instead of treating a rejected mutation as a silent stall.

If a session, leader, or tower state matters to the user, it should be representable in durable state.

## Current Major Boundaries

## Backend

`src/atc/`
- `api/` — FastAPI routes and transport layer
- `tower/` — tower lifecycle and top-level orchestration
- `leader/` — leader lifecycle and project coordination
- `session/` — shared session lifecycle orchestration
- `agents/` — provider abstractions, deploy helpers, auth/runtime-specific logic
- `state/` — DB access, models, durable state
- `terminal/` — tmux/PTy helpers and terminal-specific runtime support
- `core/` — shared infrastructure and app-level services

### Important direction for these folders

#### `agents/`
This is where provider-specific behavior should accumulate.

Examples:
- provider spawn logic
- provider startup handling
- provider readiness checks
- provider prompt delivery semantics
- provider-specific runtime helpers

This folder should grow in responsibility as provider agnosticism improves.

#### `session/`
This should orchestrate session lifecycle at the app level, not become a permanent dumping ground for provider-specific terminal behavior.

Good responsibilities:
- DB-first session creation
- status transitions
- shared orchestration flow
- retry/verification policy that is truly cross-provider

Bad responsibilities long-term:
- provider-specific startup dialog handling
- provider-specific send semantics
- provider-specific readiness parsing

If logic only exists because one backend behaves a certain way, it probably belongs in `agents/` or a provider runtime helper.

#### `terminal/`
This is infrastructure, not product logic.

Keep tmux/PTy details here or behind providers. Do not let the whole app grow implicit dependencies on terminal internals.

## Frontend

`frontend/`
- React UI for visibility, control, and feedback
- should reflect backend state clearly rather than inventing shadow workflow state
- prefer thin UI logic over duplicating orchestration rules in the client

## Desktop shell

`src-tauri/`
- packaging, desktop integration, app shell concerns
- should stay thin relative to backend/frontend business logic

## Design Principles For New Code

### Prefer narrow seams

Before adding code, ask:
- which layer should own this?
- is this provider-specific or product-generic?
- is this durable state, transport logic, or runtime wiring?

If a change needs knowledge from multiple layers, try to move that knowledge behind one interface.

### Prefer deletion over parallel systems

When a new abstraction replaces an old path, follow through.

Do not leave:
- a new provider boundary plus an almost-equivalent old hardcoded path
- two startup systems
- two readiness systems
- two prompt-delivery systems

Temporary overlap is acceptable during refactors. Permanent overlap is drift.

### Prefer explicit invariants

When a behavior matters, write it down in code, tests, or docs.

Examples:
- session rows are created before runtime spawn
- migrations are append-only
- Tower / Leader / Ace ownership boundaries stay distinct
- provider selection comes from project/session config, not scattered conditionals

## Testing Standard

We want extremely high confidence, especially in orchestration and lifecycle code.

Target posture:
- unit tests for branching logic and invariants
- integration tests for DB + lifecycle interactions
- e2e coverage for critical user flows
- regressions captured as tests before or with fixes whenever practical

### Coverage philosophy

The goal is not fake coverage. The goal is reliable behavior.

That means:
- test important state transitions
- test failure paths, not just happy paths
- test provider boundaries with mocks at the correct seam
- avoid tests that overfit obsolete internals when a new abstraction is the real contract

A good test suite should make refactors easier by protecting contracts, not harder by pinning dead implementation details.

## Code Hygiene Expectations

### Keep responsibilities local

If code in one module knows too much about another module's internals, that is a smell.

### Reduce surprise

Prefer:
- predictable naming
- small helpers with clear purpose
- explicit status changes
- obvious data flow

Avoid:
- hidden fallback behavior unless truly necessary
- broad utility modules that become junk drawers
- silent state mutation across layers

### Make cleanup part of the job

Refactors are not done when the new path works once.

They are done when:
- old redundant paths are removed or clearly marked transitional
- tests assert the new contract
- docs match reality
- follow-up seams are identified honestly

## Practical Rules For Contributors And Agents

When making changes:
1. identify the correct layer first
2. prefer provider-agnostic interfaces for agent/session behavior
3. do not add new product dependencies on raw tmux behavior unless unavoidable
4. keep DB/state transitions explicit
5. add or update tests with the change
6. if a refactor changes the real seam, update tests to match the seam, not the retired internals
7. leave the codebase simpler than you found it when possible

## Near-Term Cleanup Direction

The current provider-boundary work implies the next cleanup steps:
- continue moving provider-specific startup/readiness/send logic out of generic session orchestration paths where possible
- tighten provider tests around the real lifecycle contract
- reduce remaining duplication between shared session helpers and provider-owned runtime behavior
- keep Tower / Leader / Ace orchestration code focused on product behavior, not backend quirks

## If You Are Unsure

Ask these questions:
- is this product logic or provider/runtime logic?
- should this live in `session/`, `agents/`, `terminal/`, or `state/`?
- am I adding a second path where one should exist?
- am I testing the real contract or an old implementation detail?

If the answers are muddy, the design probably needs one more pass before the code does.

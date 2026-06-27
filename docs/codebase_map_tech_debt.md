# Executive summary

ATC has a strong conceptual architecture, but the current codebase is carrying meaningful refactor debt in three cross-cutting areas: terminology/model drift, oversized ownership boundaries, and stale documentation/contracts. The biggest risk is not one broken subsystem, it is that multiple generations of the design now coexist: `Leader` is still `manager` in many runtime contracts, legacy `Task` concepts sit beside newer `task_graphs`, and docs/UI/backend disagree on live interfaces. The code is still test-heavy, but the tests skew toward unit behavior and miss some integration seams where that drift now shows up, including at least one clearly stale method body in `tower/controller.py`. A future refactor should first normalize core nouns and state models, then split monolithic modules around clear service boundaries, then re-establish docs/API/WebSocket truth from code.

# Scope and method

Reviewed current files across:
- docs: `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/PATTERNS.md`, `docs/ANTI_PATTERNS.md`, `docs/design_logs/*`
- backend: `src/atc/api/*`, `src/atc/state/*`, `src/atc/tower/*`, `src/atc/leader/*`
- frontend: `frontend/src/*`
- tests: `tests/unit/*`, `tests/integration/*`, `tests/e2e/*`

No product code changes were made. This report is grounded in the repository as checked out.

# Priority map

## P0, architecture drift that should be stabilized before a broad refactor

### 1. Terminology drift: `Leader` in product language, `manager` in runtime/storage/API contracts

This is the most pervasive inconsistency in the codebase.

Evidence:
- design docs present the hierarchy as Tower → Leader → Ace: `docs/ARCHITECTURE.md`, `docs/design_logs/001`, `002`, `012`
- runtime model still uses `manager` in session types: `src/atc/state/models.py` (`Session.session_type: ace|manager|tower`)
- project API still exposes `/projects/{id}/manager`: `src/atc/api/routers/projects.py`, `docs/API.md`
- frontend types and fetch paths still use `manager`: `frontend/src/types/index.ts`, `frontend/src/context/AppContext.tsx`
- context layer must translate `manager` back to `leader`: `src/atc/api/routers/context.py` scope map
- cleanup, reconnect, deploy, and tests all still reason about `manager` sessions

Why it matters:
- every boundary needs translation logic or tribal knowledge
- it increases onboarding effort and makes bulk refactors riskier
- it undermines the design-log claim that naming is consistent system-wide
- it leaks into persistence, API, UI, test names, and agent deployment artifacts

Recommended refactor theme:
- choose one canonical runtime noun, almost certainly `leader`
- add a compatibility layer only at the HTTP boundary if needed
- migrate session type enums, route naming, websocket payloads, test fixtures, and deployment manifests together

### 2. Parallel task models exist, but only one seems live

ATC carries both legacy `Task` and newer `TaskGraph` concepts.

Evidence:
- `Task` dataclass remains in `src/atc/state/models.py`
- `tasks` table remains in schema and docs still describe `/api/projects/{id}/tasks`: `src/atc/state/db.py`, `docs/API.md`
- `tasks` router is a stub: `src/atc/api/routers/tasks.py`
- active orchestration flows use `task_graphs`, `task_assignments`, and `leader/orchestrator.py`
- frontend `AppState` still has `tasks: Record<string, Task[]>`, dashboard views still consume `Task[]`, and milestone logic depends on the legacy `Task` shape: `frontend/src/types/index.ts`, `frontend/src/pages/Dashboard.tsx`, `frontend/src/components/dashboard/*`, `frontend/src/utils/milestones.ts`
- `AppContext.fetchAll()` fetches `task-graphs`, not `tasks`, and never populates `state.tasks`

Why it matters:
- current ownership of “the task model” is ambiguous
- UI code is partially coupled to a dead or transitional abstraction
- any future changes to planning/execution risk duplicating work in both models

Recommended refactor theme:
- declare `task_graphs` the canonical work model, or intentionally preserve both with separate responsibilities
- remove or formally deprecate the stubbed `tasks` surface
- unify dashboard/milestone views on one status vocabulary and data source

### 3. Docs and live contracts have materially diverged

Several docs are stale enough to mislead a refactor.

Evidence:
- `docs/ARCHITECTURE.md` says the layout is “Option A” with `TowerBar` as an always-visible top bar, but design log 011 explicitly accepted a dockable overlay panel instead
- the frontend currently renders both `TowerBar` and `TowerPanel`: `frontend/src/App.tsx`
- `docs/API.md` says `DELETE /api/projects/{id}` archives the project, but the live router hard-deletes and archive is actually `PATCH /{project_id}/archive`: `src/atc/api/routers/projects.py`
- `docs/API.md` documents `/api/logs*`, while code serves `/api/failure-logs*`: `src/atc/api/routers/failure_logs.py`
- WebSocket docs still describe `manager:{project_id}`, `logs`, and binary PTY frames; live code/front-end rely on `failure_logs`, `heartbeat`, `tower`, `state`, and channel naming handled by `WsHub`/`AppContext`
- `docs/ARCHITECTURE.md` still lists `manager:{project_id}` channels

Why it matters:
- ATC explicitly depends on docs as agent-facing truth
- stale docs produce wrong generated code, wrong test assumptions, and wrong operator behavior
- this is not cosmetic drift, it affects destructive operations and realtime protocol expectations

Recommended refactor theme:
- generate or derive API/WebSocket docs from router/channel definitions where possible
- define one “runtime contract source of truth” and treat design logs as historical, not current API reference

## P1, structural debt that raises change complexity

### 4. Several core modules are doing too many jobs

The strongest examples are:
- `src/atc/state/db.py` at 2132 lines: schema bootstrap, migrations, CRUD, transition rules, cross-table deletion, feature flags, heartbeat, context, usage, budgets, GitHub, QA
- `src/atc/tower/controller.py` at 1036 lines: lifecycle state machine, leader kickoff, context seeding, budget gating, session counting, websocket broadcasting, auth detection, progress polling, respawn logic
- `src/atc/api/routers/projects.py` at 575 lines: project CRUD, leader lifecycle, context seeding, auto-kickoff, budget, GitHub
- `frontend/src/context/AppContext.tsx` at 453 lines: bootstrap fetch orchestration, reducer, websocket event decoding, normalized cache-ish state, selection state

Why it matters:
- ownership is hard to reason about
- module-level tests can still pass while seams between concerns rot
- refactors become “touch everything” changes
- domain rules are duplicated because there is no small obvious place for them

Recommended refactor theme:
- split by domain service, not just by transport
- examples: `project_service`, `leader_service`, `context_service`, `budget_service`, `ws_event_mapper`, `task_graph_repo`, `session_repo`
- keep routers thin and move workflow logic out of HTTP handlers

### 5. Transport layers contain domain workflow logic

Examples:
- project creation auto-creates a leader in the router: `projects.py`
- leader start route seeds context entries, reads project metadata, schedules retry loops, and builds kickoff prompts: `projects.py`
- `TowerController.submit_goal()` also seeds project context entries and persists package details
- context routes auto-seed from leader goal when context is empty: `context.py`

Why it matters:
- the same business workflow now exists in Tower paths, direct API paths, and fallback read paths
- it becomes unclear which path is authoritative
- behavior depends on how an action was initiated, not just what the action was

Recommended refactor theme:
- pull these behaviors behind explicit application services
- make “start leader”, “seed project context”, and “kick off goal” idempotent service operations with one owner

### 6. State vocabularies are inconsistent across layers

Examples:
- tower lifecycle uses `idle|planning|managing|complete|error`
- leader status uses `idle|planning|managing|paused|error`
- session status uses `idle|connecting|working|paused|waiting|disconnected|error`
- task graph status supports `todo|assigned|in_progress|review|done|error` in backend transitions, but frontend `TaskGraph` type only allows `todo|in_progress|done`
- dashboard milestone UI still uses legacy `Task` statuses like `pending|assigned|blocked|done|cancelled`

Why it matters:
- mapping code proliferates silently
- frontend type narrowing can become wrong without compile-time help if payloads are cast loosely
- analytics and progress summaries become harder to trust

Recommended refactor theme:
- define explicit shared enums for session, leader, tower, and task graph states
- if the frontend is intentionally narrower, encode that with mapper functions instead of divergent ad hoc types

## P2, stale abstractions and correctness risks already visible

### 7. There is at least one clearly stale or corrupted implementation in a critical controller

`tower/controller.py` contains both `_notify_tower_goal_started()` and `_on_leader_output()`. The `_on_leader_output()` body appears to be a copied version of the notification logic and references undefined names like `project_id`, `goal`, and `leader_session_id`, rather than using its `data` parameter.

Why it matters:
- this is a concrete sign of copy-paste drift in one of the highest-risk modules
- it suggests tests cover nearby behavior but not this exact path
- it weakens confidence in the surrounding controller logic even where tests exist

Recommended refactor theme:
- break the controller into small tested helpers before feature work continues there
- add targeted tests for private event handlers or move them behind public service APIs

### 8. Event and websocket contracts are manually encoded in multiple places

Evidence:
- channel names live in docs, backend broadcasters, frontend subscriptions, and reducer branches
- `AppContext.handleWsMessage()` manually interprets many loosely typed payload shapes from `state`, `tower`, `heartbeat`, `failure_logs`
- `WsHub` is intentionally generic, so semantic correctness depends on many scattered string constants

Why it matters:
- adding or renaming an event is easy to do incompletely
- drift is already visible in docs and channel names
- the frontend is tightly coupled to backend event shape details

Recommended refactor theme:
- centralize event/channel definitions and payload schemas
- consider typed server event factories plus frontend decoders per channel

### 9. Design logs are useful, but current-state ownership is blurred

The design logs are recent and thoughtful, but several accepted decisions are not reflected cleanly in live code/docs. That suggests ATC treats logs as intent, but lacks a maintenance mechanism that propagates accepted decisions into runtime contracts and reference docs.

Recommended refactor theme:
- on each architecture-affecting change, update: design log status, architecture doc, API doc, shared enums/contracts, and tests as one bundle
- use a lightweight checklist or PR template to keep those artifacts synchronized

# Testing assessment

## What is strong

- backend unit coverage is broad across many modules
- there are integration tests for context hub, websocket hub, and creation reliability
- there are frontend component and hook tests, including `AppContext` and `useWebSocket`

## Main blind spots

### 1. End-to-end coverage is shallow relative to architectural complexity

The e2e suite is small: `tests/e2e/test_ace_lifecycle.py`, `test_smoke.py`, `test_task_graph_api.py`. For a system built around Tower → Leader → Ace orchestration, that leaves major workflow seams lightly defended.

Missing or underrepresented flows:
- full goal submission from Tower through Leader kickoff to task graph progress
- restart/reconnect behavior across Tower + Leader + Ace together
- docs/API/WebSocket contract conformance tests
- project dashboard behavior against real `task_graphs` rather than legacy `tasks`

### 2. Contract drift tests are missing

Given the amount of naming and protocol drift, ATC would benefit from tests that assert:
- documented routes exist and documented destructive semantics match code
- documented websocket channels match live broadcasters/subscribers
- frontend types/decoders accept all backend-emitted statuses

### 3. Monolith modules are hard to test semantically

Some existing unit coverage proves local behavior, but not clean ownership. The stale `_on_leader_output()` body is a good example: nearby behavior can be tested while a dead or broken handler survives.

# Recommended refactor themes

## Theme A, normalize the domain language first

Do first, before major feature work:
- settle `leader` vs `manager`
- settle `task` vs `task_graph`
- publish canonical enum/state vocabularies
- add thin compatibility shims only where migration complexity requires them

## Theme B, introduce explicit service boundaries

Recommended split:
- repositories: project, session, leader, task graph, context, usage
- application services: goal submission, leader lifecycle, context seeding, project deletion, budget enforcement
- transport adapters: REST routers, websocket broadcasters, CLI handlers

This should reduce duplication between Tower, routers, and fallback paths.

## Theme C, treat docs/contracts as generated or at least centrally declared

Prioritize:
- route inventory from routers
- websocket channel inventory from broadcaster/subscriber declarations
- one canonical runtime contract doc that design logs feed into but do not replace

## Theme D, simplify frontend state ownership

- separate initial data loading from realtime event reduction
- stop carrying both `tasks` and `taskGraphs` unless both are intentional
- add typed event mappers so reducer logic is not parsing raw protocol dictionaries directly

## Theme E, add a thin architecture regression suite

Useful high-value tests:
- goal submission happy path across Tower/Leader/task graph state
- route documentation parity smoke test
- websocket channel parity smoke test
- dashboard/task model parity test proving UI reads the live work model

# Suggested execution order

1. Freeze vocabulary and publish a migration map (`manager` → `leader`, `tasks` → `task_graphs` where applicable)
2. Extract service boundaries from `projects.py`, `tower/controller.py`, and `state/db.py`
3. Replace duplicated workflow logic with shared application services
4. Update docs from live contracts
5. Add architecture regression tests around routes, channels, and state enums
6. Then do deeper subsystem refactors, especially Tower/Leader orchestration and dashboard state

# File hotspots worth targeting in a future refactor

- `src/atc/state/db.py`
- `src/atc/tower/controller.py`
- `src/atc/api/routers/projects.py`
- `src/atc/api/routers/context.py`
- `frontend/src/context/AppContext.tsx`
- `frontend/src/types/index.ts`
- `frontend/src/pages/Dashboard.tsx` and `frontend/src/components/dashboard/*`
- `docs/API.md`
- `docs/ARCHITECTURE.md`

# Bottom line

ATC is not suffering from a lack of architecture. It is suffering from accumulated architecture overlap. The next refactor will go much better if it starts by collapsing duplicate language and state models, then carving transport and workflow concerns apart, rather than trying to optimize individual modules in place.
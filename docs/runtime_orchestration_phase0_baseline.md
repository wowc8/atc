# Runtime/Orchestration Phase 0 Baseline

**Status:** in progress
**Last updated:** 2026-06-07 04:03 UTC
**Parent plan:** `docs/runtime_orchestration_refactor_phases.md`

## Purpose

Phase 0 establishes the current ATC runtime/orchestration behavior before the shared delivery trace, hardened runner, lifecycle-state, and reconcile phases change behavior. This document is the phase evidence bundle: baseline validation, current delivery/session path map, existing test coverage inventory, gaps, and the Playwright baseline gate.

Phase 0 must preserve the product hierarchy:

```text
Operator / assistant → Tower → Leader → Ace
```

Direct Leader/Ace inspection is allowed for diagnosis, but the baseline workflow and future scenario tests should drive work through Tower unless explicitly testing a lower-level boundary.

## Phase 0 acceptance criteria

- [x] Local validation evidence exists for:
  - Tower Start with no active project.
  - Codex as the new/default provider path.
  - Tower-driven Leader session creation without normal assistant-to-Leader shortcutting.
  - Ace orchestration path is mapped; full Ace spawn/run is deferred to later scenario hardening because Phase 0 is a baseline/no-behavior-change phase.
- [x] Current delivery/session paths are mapped below.
- [x] Role boundaries are recorded in repo docs:
  - `docs/agents/TOWER.md`
  - `docs/agents/LEADER.md`
  - `docs/agents/ACE.md`
  - `docs/runtime_orchestration_refactor_phases.md`
- [x] Existing tests and scenario gaps are inventoried below.
- [ ] Post-merge Playwright evidence exists under `test-results/phase0-baseline-*` from the merged commit.

## Current instruction and session path map

### 1. Tower Start with no active project

Primary UI/API path:

```text
frontend TowerPanel Start
→ POST /api/tower/start with optional/null project_id
→ atc.api.routers.tower.start_tower()
→ TowerController.start_session(project_id=None)
→ atc.tower.session.start_tower_session(project_id=None)
→ _resolve_tower_project_id()
→ create/reuse sentinel "Tower Workspace" project if needed
→ db_ops.create_session(session_type="tower", provider=current default, scope_type="global")
→ deploy_tower_files()
→ _spawn_provider_session(session_type="tower")
→ RuntimeService.spawn_existing_session(StartRoleRequest(role=TOWER))
→ provider runtime
→ shared tmux substrate/pane
```

Important current files:

- `frontend/src/components/tower/TowerPanel.tsx`
- `src/atc/api/routers/tower.py`
- `src/atc/tower/controller.py`
- `src/atc/tower/session.py`
- `src/atc/session/ace.py` (`_spawn_provider_session` compatibility boundary)
- `src/atc/runtime/service.py`
- `src/atc/providers/*/runtime.py`
- `src/atc/runtime/tmux/substrate.py`

Baseline expectation: Tower start is global/operator-facing. It should not require a selected active project, although the sessions table still needs a project FK and therefore uses an existing active project or the sentinel Tower Workspace as an anchor.

### 2. Codex default provider path

Current default provider sources:

```text
config.yaml / live settings
→ atc.config.AgentProviderConfig.default
→ app connection state via get_connection_app_state()
→ Tower/Leader/Ace session creation stamps provider on the session row
→ provider registry/runtime resolves the stamped provider
```

Current behavior to preserve:

- new/default provider paths should default to `codex`, not `claude_code`;
- a session row's provider identity is immutable for that row;
- provider switches should create replacement sessions instead of mutating old rows;
- Tower, Leader, and Ace creation should consult the live settings/default provider where appropriate.

Important current files:

- `config.yaml`
- `src/atc/config.py`
- `src/atc/api/routers/settings.py`
- `frontend/src/components/settings/*`
- `src/atc/tower/session.py`
- `src/atc/leader/leader.py`
- `src/atc/session/ace.py`
- `src/atc/providers/registry.py`
- `src/atc/providers/codex/runtime.py`

### 3. Tower-driven project goal and Leader startup

Primary path:

```text
Operator submits goal through Tower UI/API
→ POST /api/tower/goal
→ atc.api.routers.tower.submit_goal()
→ TowerController.submit_goal(project_id, goal)
→ build_context_package()
→ persist/update project context entries
→ start_leader(project_id, goal, context_package)
→ db_ops.create_session(session_type="manager", provider=current default)
→ deploy_manager_files()
→ RuntimeService.prepare_workspace(role=LEADER)
→ _spawn_provider_session(session_type="manager")
→ RuntimeService.spawn_existing_session(StartRoleRequest(role=LEADER))
→ provider runtime/tmux pane
→ TowerController._send_leader_kickoff()
→ Leader terminal receives kickoff
```

Important current files:

- `src/atc/api/routers/tower.py`
- `src/atc/tower/controller.py`
- `src/atc/leader/context_package.py`
- `src/atc/leader/leader.py`
- `src/atc/agents/deploy.py`
- `src/atc/session/ace.py`
- `src/atc/runtime/service.py`

Baseline expectation: normal external/assistant work enters through Tower. Tower is responsible for creating/routing the Leader; assistants should not directly task Leaders as the normal product workflow.

### 4. Leader-driven Ace assignment

Primary path after Leader decomposes tasks:

```text
Leader creates/updates task graph via Leader/API tools
→ LeaderOrchestrator.spawn_aces_for_ready_tasks()
→ LeaderOrchestrator._spawn_ace_for_task()
→ create_ace(project_id, task_id, deploy_spec_kwargs)
→ db_ops.create_session(session_type="ace", provider=current default)
→ deploy_ace_files()
→ RuntimeService.prepare_workspace(role=ACE)
→ _spawn_provider_session(session_type="ace")
→ RuntimeService.spawn_existing_session(StartRoleRequest(role=ACE))
→ db_ops.assign_task(idempotency_key)
→ start_ace() / task instructions delivered through Ace runtime path
```

Important current files:

- `src/atc/leader/orchestrator.py`
- `src/atc/leader/decomposer.py`
- `src/atc/session/ace.py`
- `src/atc/agents/deploy.py`
- `src/atc/state/db.py`
- `src/atc/runtime/service.py`

Baseline expectation: Leaders supervise Aces. Operators/Tower can inspect, but the normal work assignment chain stays Tower → Leader → Ace.

### 5. Direct terminal/message compatibility paths still present

The current baseline still contains compatibility paths that later phases should observe before replacing:

- `src/atc/tower/session.py::send_tower_message()` uses `_send_keys()` directly after pane/status checks.
- `src/atc/session/ace.py::_send_keys()` remains a legacy atomic text+Enter tmux helper.
- `src/atc/terminal/pty_stream.py` exposes WebSocket terminal key/instruction forwarding.
- `src/atc/api/routers/aces.py` still has a direct Ace message endpoint path.

Phase 1/2 should add trace visibility and a shared runner around these seams before deleting or rewriting them.

## Existing relevant automated coverage

Current tests that defend Phase 0-adjacent behavior include:

- Tower/session behavior:
  - `tests/unit/test_tower_controller.py`
  - `tests/unit/test_tower_session_reuse.py`
  - `tests/unit/test_tower_respawn.py`
  - `tests/unit/test_lazy_tower_start.py`
- Provider/default/runtime behavior:
  - `tests/unit/test_agent_provider.py`
  - `tests/unit/test_codex_provider.py`
  - `tests/unit/test_codex_runtime.py`
  - `tests/unit/test_provider_registry.py`
  - `tests/unit/test_runtime_service.py`
  - `tests/unit/test_reconnect_runtime_semantics.py`
- Leader/Ace/task orchestration:
  - `tests/unit/test_leader_api.py`
  - `tests/unit/test_leader_orchestrator.py`
  - `tests/unit/test_orchestration_service.py`
  - `tests/unit/test_orchestration_router.py`
  - `tests/unit/test_orchestration_idempotency.py`
  - `tests/e2e/test_ace_lifecycle.py`
  - `tests/e2e/test_task_graph_api.py`
- Delivery/retry/reconnect/trust support:
  - `tests/unit/test_creation_reliability.py`
  - `tests/integration/test_creation_reliability.py`
  - `tests/unit/test_pty_stream.py`
  - `tests/unit/test_trust_dialog.py`
  - `tests/unit/test_ace_trust_dialog.py`
  - `tests/unit/test_welcome_hardening.py`

## Gaps to carry into later phases

- No structured delivery trace event model yet; Phase 1 should add it before runner extraction.
- Existing API/UI responses can still imply success before provider/TUI delivery is fully verified.
- The same tmux write/submit/verify semantics are still spread across compatibility helpers and role-specific paths.
- Scenario coverage needs a durable Tower-driven end-to-end regression suite that can run without destructive real project changes.
- Direct lower-level debug/compatibility endpoints need explicit trace/reason-code behavior before cleanup.

## Phase 0 Playwright baseline gate

The script imports Playwright from `frontend/node_modules`, so install frontend dependencies before running it:

```bash
cd frontend && PATH="/opt/homebrew/bin:$PATH" npm install
cd .. && PATH="/opt/homebrew/bin:$PATH" ATC_UI_URL=http://localhost:5173 ATC_API_URL=http://127.0.0.1:8420 node scripts/playwright/phase0-baseline.mjs
```

The script records screenshots and `report.json` under:

```text
test-results/phase0-baseline-<timestamp>/
```

It verifies:

- backend Tower status endpoint responds;
- dashboard route and dashboard view toggles load;
- provider settings can be set/read as `codex`;
- Tower Start is visible/enabled without selecting a project;
- Tower starts and reports a session id with no active project selected;
- a temporary Codex project can be created through the UI;
- project route loads with Tower panel;
- a Tower-scoped project goal is accepted;
- the Tower-driven flow creates a Leader session.

This is intentionally a baseline/regression smoke, not the final Phase 8 scenario suite.

## Local validation log

### 2026-06-07 04:10 UTC — pre-merge Phase 0 baseline

Environment:

- Worktree: `/tmp/atc-work`
- Branch: `matthew/phase0-baseline-validation`
- Backend: `http://127.0.0.1:8420`
- Frontend: `http://localhost:5173`
- Provider default during UI validation: `codex`
- Codex command used by settings baseline: `/Users/mcole_studio/.local/bin/codex --dangerously-bypass-approvals-and-sandbox`

Passing checks:

- `python -m pytest tests/unit/test_tower_controller.py tests/unit/test_lazy_tower_start.py tests/unit/test_agent_provider.py tests/unit/test_codex_provider.py tests/unit/test_codex_runtime.py tests/unit/test_runtime_service.py tests/unit/test_orchestration_service.py` — passing subset from the broader targeted run.
- `cd frontend && npm run test -- --run src/components/tower/__tests__/TowerPanel.test.tsx src/components/settings/__tests__/*.test.tsx` — 11 TowerPanel tests passed; settings glob matched no additional files in this checkout.
- `node --check scripts/playwright/phase0-baseline.mjs` — passed.
- `ATC_UI_URL=http://localhost:5173 ATC_API_URL=http://127.0.0.1:8420 node scripts/playwright/phase0-baseline.mjs` — passed all scripted checks.

Playwright evidence:

- Report: `test-results/phase0-baseline-2026-06-07T04-10-23-752Z/report.json`
- Screenshots:
  - `00-dashboard.png`
  - `01-dashboard-board-toggle.png`
  - `02-settings-codex.png`
  - `03-tower-started-no-active-project.png`
  - `04-project-tower-panel.png`
  - `05-tower-goal-leader-created.png`

Observed baseline warnings/gaps from Playwright report:

- Vite/dev WebSocket warnings: `WebSocket connection to 'ws://localhost:5173/ws' failed: WebSocket is closed before the connection is established.`
- Page error observed multiple times: `Cannot read properties of undefined (reading 'dimensions')`.
- One aborted manager fetch during route transition: `/api/projects/<project>/manager` returned `net::ERR_ABORTED`.

Known existing test/build failures observed during Phase 0 validation (not introduced by this docs/script baseline branch):

- Broader targeted pytest command failed in existing orchestration/ace tests:
  - `tests/unit/test_leader_orchestrator.py::TestSpawnAces::test_uses_project_agent_provider`
  - `tests/unit/test_leader_orchestrator.py::TestSpawnAces::test_default_provider_uses_claude`
  - `tests/unit/test_leader_orchestrator.py::TestSpawnRetryAssignmentReuse::test_reuses_terminal_assignment_without_todo_to_in_progress_jump`
  - `tests/e2e/test_ace_lifecycle.py::TestAceLifecycle::test_full_lifecycle`
- `cd frontend && npm run build` failed with existing TypeScript error:
  - `src/components/tower/__tests__/TowerPanel.test.tsx(80,48): error TS6133: 'init' is declared but its value is never read.`

Post-merge validation: pending until this branch is merged.

# Leader Kickoff Verification and Startup Prompt Recovery Plan

**Status:** In progress — Phases 0–6 implemented through PR #320 follow-ups; Phase 6 deploys provider-neutral local ATC API capability metadata/helpers for managed workspaces without broad external-network approval.
**Issue:** [#297](https://github.com/wowc8/atc/issues/297)
**Last updated:** 2026-06-13
**Scope:** Follow-up runtime/orchestration hardening for Leader startup, managed-workspace provider prompts, `prompt_not_submitted` recovery, task graph ergonomics, and local ATC API capability setup.

## Why this plan exists

A live ATC run exposed a gap that the completed runtime truth work made visible but did not fully close: ATC could report a Leader as started while the provider TUI was still sitting at a trust/startup prompt. The runtime/session existed, but the Leader had not accepted the goal, had not begun actionable work, and had not created a task graph.

The fix is not to teach Tower about Codex prompts. The fix is to tighten the provider-neutral kickoff contract and make provider adapters responsible for prompt classification and safe prompt handling.

## Non-negotiable architecture boundaries

Normal command flow remains:

```text
Operator / assistant -> Tower -> Leader -> Ace
```

Provider separation remains:

- Product/orchestration layers consume neutral fields: `runtime_state`, `delivery_state`, `kickoff_state`, `blocker_reason`, `recovery_recommendation`, `last_activity_at`, and redacted nested diagnostics.
- Provider adapters/classifiers own exact terminal text matching, key sequences, trust/startup prompt mechanics, and provider capability declarations.
- REST remains the canonical product/backend API. The `atc` CLI is a thin agent/operator wrapper. `atc-mcp` is an optional adapter. None of the front doors should fork runtime truth, task graph, or recovery semantics.
- A session row, tmux pane, visible prompt text, `202 Accepted`, `queued`, `submitted`, or `sent` response is not proof that a Leader accepted a goal.

## Desired end state

Starting a Leader with a goal should expose a staged kickoff truth model:

| State | Meaning |
|---|---|
| `session_created` | DB/session/runtime shell exists; not proof of provider acceptance. |
| `startup_handshake_ready` | Provider adapter classified the runtime as safe/ready for ATC input. |
| `payload_written` | Kickoff payload was written to the PTY/runtime substrate. |
| `submit_sent` | Enter/submit was sent for the kickoff payload. |
| `submitted_pending_acceptance` | Submission happened, but no provider/activity proof exists yet. |
| `goal_accepted` | Provider-neutral evidence shows the Leader accepted the goal. Preferred proof is a Leader-originated active/goal-accepted report through the canonical ATC reporting path. |
| `leader_reported_active` | The Leader explicitly reported active before beginning task work. This is the deterministic handshake Tower should prefer over terminal-output inference. |
| `first_actionable_step_observed` | Leader produced an actionable plan/status/tool use, or equivalent runtime evidence after the active report. |
| `task_graph_created` | A project task graph/milestone/task was created or updated through canonical services. |
| `blocked` | Runtime/kickoff is blocked with a stable `blocker_reason` and recovery guidance. |
| `failed` | Startup/kickoff failed unexpectedly. |

Tower should not enter normal low-frequency monitoring until the Leader reaches `goal_accepted` / `leader_reported_active` plus either `first_actionable_step_observed` or `task_graph_created`, or until a classified blocker is reported. If the Leader does not emit the active/goal-accepted report during the startup window, Tower should keep the state as `submitted_pending_acceptance`, `kickoff_unverified`, or `blocked`; it should not call the Leader `working`.

## Phase 0 — Baseline reproduction and contract audit

**Goal:** Capture the current failure mode and map every kickoff/health/task graph path before changing behavior.

### Work

1. Reproduce or simulate the observed Leader startup stall:
   - runtime/session exists;
   - provider prompt or local approval blocks execution;
   - kickoff/progress remains unverified;
   - task graph count remains zero.
2. Inventory current Leader start paths across:
   - Tower project/goal submission;
   - orchestration service and REST endpoints;
   - `atc` CLI commands;
   - provider runtime startup/handshake;
   - health/recovery endpoints;
   - task graph creation APIs.
3. Record the current response fields that can overstate truth.
4. Identify existing tests that should fail after the stronger contract is expressed.
5. Update docs with the audited path map if the implementation surface differs from this plan.

### Acceptance criteria

- A baseline note or test fixture demonstrates session-exists-but-goal-not-accepted behavior.
- The current kickoff truth fields and gaps are documented.
- Implementation files/tests to touch are listed before behavior changes begin.

### Validation

- Targeted backend test or scripted smoke captures blocked/unverified Leader startup evidence.
- Project docs and ATC repo docs link to this plan.

## Phase 1 — Provider-neutral kickoff state model

**Goal:** Add explicit kickoff truth without changing provider prompt mechanics yet.

### Work

1. Add provider-neutral kickoff fields/models, for example:
   - `kickoff_state`;
   - `kickoff_verified`;
   - `startup_handshake_state`;
   - `goal_acceptance_state`;
   - `first_actionable_step_observed_at`;
   - `task_graph_created_at`;
   - `kickoff_blocker_reason`;
   - `kickoff_recovery_recommendation`.
2. Persist the original Leader kickoff payload as replayable source-of-truth context.
3. Ensure trace events can correlate startup handshake, payload write, submit, Leader active/goal-accepted report, acceptance observation, blocker, and task graph creation under one trace id.
4. Keep responses additive/backward-compatible, but stop newly-added fields from using optimistic wording.
5. Update REST/CLI/MCP schemas or docs to describe the additive fields.

### Acceptance criteria

- API/CLI can distinguish session creation from goal acceptance.
- `kickoff_verified` is false until provider-neutral acceptance evidence exists.
- Persisted kickoff payload can be retrieved for recovery planning.
- Tests cover serialization, default states, and blocked/unverified states.

### Validation

- Unit tests for kickoff state model and trace construction.
- API contract tests for Leader start/progress response shape.
- Docs updated in `docs/API.md` if response fields change.

## Phase 2 — Managed workspace startup handshake and safe trust handling

**Goal:** Make managed ATC workspaces reach a provider-ready state before ATC sends kickoff instructions, while keeping prompt mechanics provider-owned.

### Work

1. Add or extend provider adapter capability metadata:
   - can classify trust prompt;
   - can auto-accept managed-workspace trust prompt;
   - can classify local API approval prompt;
   - can pre-authorize local ATC API access;
   - can distinguish auth/secret/unknown permission prompts that must not be auto-accepted.
2. Move any Codex-specific trust/startup prompt detection into the Codex classifier if it is not already there.
3. Add shared startup handshake sequencing in the runtime service. The happy-path assumption for managed workspaces is that a provider startup/trust prompt may appear, so the handshake should proactively look for it and resolve it before kickoff. If no trust prompt appears, that is also a valid provider-ready path and must be handled explicitly:
   - spawn runtime;
   - wait briefly for TUI draw;
   - inspect via provider classifier;
   - if a safe provider-declared managed-workspace trust prompt is present, auto-resolve it;
   - if no trust prompt is present and the classifier reports ready/idle input, continue without recovery;
   - if the provider uses a trusted-workspace launch flag or cached trust state, treat the resulting no-prompt startup as ready only after classifier confirmation;
   - re-inspect after any auto-resolution attempt;
   - return ready/blocked/failed truth.
4. Ensure auth/login/secrets/unknown permission prompts remain blockers.
5. Emit audit/trace events for every trust-prompt expectation path: prompt observed and resolved, prompt expected but absent and ready, prompt absent but not ready, and prompt observed but unsafe.

### Acceptance criteria

- Leader kickoff is not written behind a classified startup/trust/auth/permission blocker.
- Startup handshake treats managed-workspace trust as an expected startup branch: prompt present and safely auto-resolved, or prompt absent and explicitly confirmed ready/idle, both before kickoff payload delivery.
- Provider-specific prompt strings remain inside provider adapter/classifier code and tests.
- Startup blocked states include clear `blocker_reason` and `recovery_recommendation`.

### Validation

- Provider tests with mocked Codex startup/trust/auth/permission excerpts, including trust prompt present, trust prompt absent because workspace is already trusted, and trust prompt absent because a trusted-workspace launch capability was used.
- Runtime service tests for ready, safe auto-trust, unsafe permission, and unknown blocker paths.
- Static search confirms Tower/Leader/API/CLI do not match Codex prompt strings.

## Phase 3 — Strong Leader kickoff verification

**Goal:** Leader start reports goal accepted and running only after provider-neutral evidence, not merely after a session exists or submit was sent.

### Work

1. Wire Leader start/Tower kickoff through the Phase 1 state model and Phase 2 handshake.
2. After submit, require the Leader to emit an explicit active/goal-accepted report through the canonical ATC reporting path before starting task work. This report is the preferred proof that the Leader accepted the kickoff.
3. After the active report, observe for working evidence such as:
   - first actionable Leader status/event;
   - canonical task graph/milestone/task creation;
   - hook/context helper confirmation from the deployed Leader workspace;
   - provider active output/reasoning only as supporting/fallback evidence, not as the primary deterministic proof.
4. Treat these as invalid success signals:
   - session row exists;
   - pane exists;
   - payload text is visible but not submitted;
   - provider starter/default prompt is still visible;
   - transport response says `queued`, `submitted`, or `sent` with no acceptance evidence;
   - provider output appears active but no Leader active/goal-accepted report or canonical work-state update was observed within the startup window.
5. Update Tower startup cadence so normal monitoring starts only after verified kickoff or classified blocker.
6. Surface Leader health states such as:
   - `starting`;
   - `blocked_on_provider_prompt`;
   - `kickoff_unverified`;
   - `goal_accepted`;
   - `leader_reported_active`;
   - `task_graph_empty`;
   - `working` only after active report plus actionable step or task graph evidence;
   - `failed`.

### Acceptance criteria

- `atc leader start --goal ...` and corresponding REST responses expose kickoff state and verification truth.
- Leader role instructions require an explicit active/goal-accepted report before task work begins.
- Tower reports blocked/unverified kickoff instead of silently showing only zero tasks.
- Tests cover success, missing active report, prompt-not-submitted, default prompt, trust/auth/permission blocker, missing pane, and no-task-graph-yet cases.

### Validation

- Caller-boundary tests for Tower startup behavior.
- CLI/API examples for healthy, blocked, and unverified startup.
- Playwright/API evidence showing the Project/Tower UI reports the health reason.

## Phase 4 — Inspect-first `prompt_not_submitted` recovery

**Goal:** Recover safely from the specific class where the kickoff payload is present but not submitted, or where the persisted kickoff payload can be replayed safely.

### Work

1. Extend recovery planning for `prompt_not_submitted` with explicit safety checks:
   - current pane belongs to the expected session/project/provider;
   - latest prompt region contains the expected pending payload unchanged; or
   - no unsafe visible prompt exists and persisted kickoff payload can be replayed;
   - runtime is not auth/permission/unknown blocked.
2. Add dry-run recovery output that explains exactly what would happen.
3. Add apply modes gated by explicit policy, for example:
   - `submit_visible_kickoff_payload`;
   - `resend_persisted_kickoff_payload`.
4. Emit audit events for skipped, applied, blocked, and failed recovery actions.
5. Re-verify kickoff after recovery instead of declaring success after pressing Enter/resending.

### Acceptance criteria

- `prompt_not_submitted` has a recovery plan with `safe_to_apply` based on inspection, not on the fact that the user requested apply.
- Apply refuses if the pending prompt text is different, stale, provider-blocked, auth-blocked, or ambiguous.
- Successful recovery transitions through submit/acceptance verification before Tower treats Leader as active.

### Validation

- Unit/service tests for dry-run and apply refusal/success cases.
- CLI tests for `atc leader recover --dry-run` and apply policy flags.
- Manual/API evidence for a simulated pending kickoff recovery.

## Phase 5 — First-class task graph commands for Leaders

**Goal:** Leaders should not need to inspect OpenAPI manually to create the initial task graph.

### Implemented contract

- `atc tasks create/list/assign` are thin CLI wrappers over task graph REST/Leader assignment endpoints.
- `atc leader bootstrap-tasks` creates a simple initial task graph without requiring Leaders to inspect OpenAPI.
- `POST /api/projects/{project_id}/leader/assign-task` delegates to Leader orchestration for single-task assignment; provider/runtime mechanics remain behind Leader/session/runtime boundaries.
- Generated Leader workspace instructions prefer the task graph CLI path and keep raw REST/OpenAPI inspection as a fallback only.

### Work

1. Add CLI commands as thin wrappers over canonical REST/service behavior, for example:
   - `atc tasks create --project-id ... --title ... [--description ...]`;
   - `atc tasks assign --project-id ... --task-id ... [--ace-id ...]`;
   - `atc leader bootstrap-tasks --project-id ... --goal ...`.
2. Keep REST/service as the canonical product API; CLI should not duplicate task graph logic.
3. Update Leader role docs/instructions to prefer the task graph CLI helpers for common operations.
4. Ensure command output includes stable IDs and runtime/task state truth needed by Leader/Tower.
5. Consider optional default graph creation during project creation or `leader start --goal`, but keep it explicit/configured until the UX is proven.

### Acceptance criteria

- Leaders have documented commands for creating and assigning tasks without reading OpenAPI.
- CLI task commands route to canonical services and return structured JSON/table output.
- Role docs and API docs explain the intended path.

### Validation

- CLI unit/integration tests for create/assign/bootstrap commands.
- Leader role-doc checks include the new commands.
- Scenario smoke shows a Leader can bootstrap a simple task graph through first-class helpers.

## Phase 6 — Local ATC API capability preauthorization for managed agents

**Goal:** Managed Leader/Ace agents can inspect the local ATC API without interactive provider approval, while not gaining broad network approval.

### Implemented contract

- Managed Tower/Leader/Ace deployment writes `.atc/local_api_capability.json` for local ATC API inspection when the configured API base URL resolves to `127.0.0.1` or `localhost`.
- The capability declares `external_network_allowed: false`, a local host allowlist, bounded methods, and ATC/OpenAPI path prefixes only.
- `.atc/local_api.sh` validates method/path against the capability file before issuing a request; disallowed paths fail locally without network access.
- Provider-neutral AGENTS/CLAUDE instructions tell managed agents to use the scoped helper or `atc` CLI for local API inspection and explicitly state that external network access is not authorized.
- Provider adapters remain responsible for any provider-specific approval or trust mechanics; orchestration/deploy code only emits neutral capability metadata, helper files, and instructions.

### Work

1. Define a provider-neutral managed-workspace capability such as `local_atc_api_inspection` with fields:
   - host allowlist: `127.0.0.1`, `localhost`;
   - configured ATC port/base URL;
   - allowed methods/paths for docs/context/task graph/status as needed;
   - no external-network wildcard.
2. Have provider adapters translate this capability into provider-specific approval/config mechanisms when supported.
3. Fall back to a clear blocker/recommendation when a provider cannot preauthorize the capability.
4. Update generated Leader/Ace workspace instructions to use the approved local API/CLI path.
5. Keep secrets out of diagnostics and generated docs.

### Acceptance criteria

- Local API inspection for managed agents does not stop on a provider approval prompt when the provider supports the capability.
- Capability is scoped to local ATC API access and does not authorize arbitrary outbound requests.
- Unsupported provider paths fail clearly with a neutral blocker/recommendation.

### Validation

- Provider adapter tests for capability translation/refusal.
- Generated workspace file tests confirm allowed local ATC API guidance exists.
- Runtime smoke verifies API docs/context access does not require manual prompt approval in supported providers.

## Phase 7 — UI/CLI health surfacing and operator recovery guidance

**Goal:** Operators see the real Leader health state and an actionable recovery path instead of only zero progress/tasks.

### Work

1. Add/adjust health response fields for Project/Tower/Leader surfaces:
   - `leader_state`;
   - `kickoff_state`;
   - `runtime_state`;
   - `delivery_state`;
   - `blocker_reason`;
   - `recovery_recommendation`;
   - `recommended_command`;
   - redacted provider diagnostics.
2. Update UI copy/tooltips to explain blocked/unverified startup.
3. Add CLI health output that names the recovery command and policy needed.
4. Ensure healthy Leader monitoring remains quiet/backed off once kickoff is verified.

### Acceptance criteria

- A blocked Leader startup is visible as `blocked_on_provider_prompt`/specific neutral blocker, not just `0 tasks`.
- UI and CLI show a safe next action.
- Tower does not spam/nudge a provider-blocked Leader as if it were ignoring instructions.

### Validation

- API tests for health shape.
- Playwright screenshots for healthy, unverified, and blocked Leader startup states.
- Baseline smoke confirms normal Project/Tower navigation still works.

## Phase 8 — Scenario regression and post-merge evidence loop

**Goal:** Lock the behavior with a scenario suite that matches the original field failure and verifies the full Tower -> Leader -> Ace chain.

### Work

1. Add scenario tests for:
   - managed-workspace trust prompt auto-resolution;
   - auth/permission prompt refusal;
   - `prompt_not_submitted` dry-run and safe apply;
   - Leader kickoff acceptance with first actionable step;
   - task graph bootstrap through CLI/helper;
   - local ATC API inspection capability;
   - Tower health display for blocked startup.
2. Run a Tower-driven Leader/Ace execution-truth path:
   - Tower starts Leader;
   - Leader creates/reuses an Ace for a task;
   - task assignment points to live `ace` session;
   - runtime evidence shows Ace received/submitted/accepted work or is blocked with classified reason.
3. Update docs after each phase as code/API behavior changes.
4. Post Playwright screenshots/reports to PRs when available.

### Acceptance criteria

- The original field failure is covered: session exists but kickoff is blocked/unaccepted must not be reported as healthy/running.
- Full chain validation does not rely on `202 Accepted`, `sent`, or session rows as execution proof.
- Docs match implementation behavior at merge time.

### Phase 8 implementation notes

- `tests/e2e/test_phase8_scenarios.py` is the canonical scenario regression suite for this Leader kickoff recovery plan. It now covers the original field failure, prompt blockers before PTY writes, dry-run/safe apply recovery coverage through the runtime-health unit contract, Leader active-report plus task-graph proof, task bootstrap helpers, local ATC API capability files, and Tower-driven Leader/Ace execution truth.
- Scenario acceptance requires canonical evidence beyond a session row: Leader health must expose unaccepted/blocked kickoff state until the Leader reports goal acceptance and task graph evidence exists; Ace validation must preserve assignment plus runtime delivery/blocker evidence.
- Post-merge evidence for this phase must include the Phase 8 scenario suite, targeted runtime/CLI/deploy coverage, a live API/CLI evidence artifact, and Mac Studio Playwright changed-behavior/baseline screenshots.

### Validation

- Targeted backend/CLI/frontend tests pass for changed behavior.
- Mac Studio Playwright changed-behavior and baseline smoke evidence is captured.
- Post-merge validation is rerun from `main`.

## Phase 9 — Contract/documentation convergence

**Goal:** Make the durable API docs, role contracts, and validation tests match the runtime truth behavior implemented through Phase 8 so future agents do not regress to session-row or provider-prompt assumptions.

### Work

1. Update API docs for Leader health, report-active, and inspect-first recovery endpoints.
2. Update Tower/Leader/Ace role contracts with the runtime truth responsibilities each role must follow.
3. Document first-class task helpers and local ATC API helper expectations in the Leader-facing contract.
4. Add documentation contract tests that fail if critical runtime truth fields, recovery commands, or role boundaries disappear from the docs.

### Acceptance criteria

- Docs and role contracts encode the same runtime truth rules as the code: normal monitoring still requires kickoff/task-graph truth rather than a session row.
- Documentation contract tests pass and cover API, Tower, Leader, Ace, and the recovery plan.
- Provider boundaries remain explicit: product docs refer to provider-neutral blockers/guidance; exact prompt strings and key sequences stay in adapters/classifiers.

### Validation

- Documentation contract tests pass with the targeted runtime/API/CLI suites.
- Baseline UI smoke still passes, with screenshots attached to the PR when PR evidence exists.
- Post-merge validation is rerun from `main`.

## Documentation requirements for every phase

Every implementation PR for this plan must check and update the relevant docs before merge:

- `docs/leader_kickoff_recovery_plan.md` — phase status/notes and deviations from this plan.
- `docs/runtime_truth_recovery_plan.md` — canonical runtime truth model if states/recovery policies change.
- `docs/ARCHITECTURE.md` — orchestration/provider/front-door boundary changes.
- `docs/API.md` — REST response/request behavior or health/recovery fields.
- `docs/agents/LEADER.md`, `docs/agents/TOWER.md`, `docs/agents/ACE.md` — role instructions and task graph/API/CLI guidance.
- `docs/START_HERE.md` and `docs/CODEBASE_MAP.md` — links/index updates for new durable docs.
- `projects/atc.md` in the Skunkworks/ColeClaw project tracker — roadmap status, open questions, and decisions.

Do not mark a phase complete until docs, tests, PR review, merge, and post-merge validation are all done.

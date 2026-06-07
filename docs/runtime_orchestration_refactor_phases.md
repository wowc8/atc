# ATC Runtime/Orchestration Hardening Refactor Phases

**Status:** In progress / planning-to-implementation handoff
**Last updated:** 2026-06-07 03:47 UTC
**Source:** `projects/atc.md` refactor plan plus Matthew's Tower → Leader → Ace hierarchy clarification

## Purpose

This document turns the ATC Runtime/Orchestration Hardening refactor into implementable phases. The refactor target is the runtime/orchestration layer above tmux: instruction truth, session lifecycle ownership, recovery, and role-correct Tower → Leader → Ace coordination.

The goal is not to replace tmux. The goal is to make the shared tmux-backed runtime boundary truthful, observable, provider-neutral, and safe for Tower, Leader, and Ace workflows.

## Non-negotiable hierarchy

Normal operator/assistant command flow must remain:

```text
Operator / assistant → Tower → Leader → Ace
```

- Operators and external assistants normally communicate only with **Tower**.
- **Tower** manages Leaders, planning loops, new project orchestration, and global resources.
- **Leaders** manage project decomposition, phases, task graphs, Ace assignment, and Ace supervision.
- **Aces** execute scoped tasks and report evidence/blockers upward.

Debugging, recovery, and implementation tests may inspect lower levels directly, but the product workflow must not flatten the hierarchy.

## Required phase execution and QA loop

Each implementation phase is not complete until it has gone through the full coding loop:

1. plan the phase slice and acceptance criteria;
2. code the slice on a branch;
3. run targeted unit/integration checks;
4. open the PR;
5. review the PR;
6. merge the PR;
7. validate the merged result locally; and
8. ask whether anything else remains for the phase before moving to the next phase.

Phase validation must include Playwright verification on the Mac Studio for both:

- **Changed behavior:** every UI-visible or workflow-visible behavior touched by the phase, with screenshots and a written pass/fail report.
- **Baseline regression coverage:** a stable smoke path that proves core ATC functionality has not drifted, including app boot, dashboard/project navigation, Tower as the operator-facing surface, Codex/default-provider expectations, Tower Start without an active project, and a Tower-driven Leader/Ace orchestration path when safe to run.

Playwright evidence should be stored under a timestamped project-local `screenshots/` or `test-results/` directory and reported with absolute paths. Console errors, failed network requests, blocked provider/runtime prompts, and any skipped destructive workflow steps must be recorded explicitly. If Playwright cannot complete because of environment/auth/provider state, that blocker must be treated as unfinished phase work, not ignored.

## Phase 0 — Baseline validation and docs alignment

**Goal:** Establish the current runtime behavior, preserve the latest Codex-default/Tower-global fixes, and ensure docs agree before invasive changes.

**Work:**
- Validate the June 7 fixes together on a local dev run:
  - Tower Start works with no active project.
  - ATC defaults to Codex instead of Claude for new/default provider paths.
  - Tower-driven flow can create/manage Leaders without direct assistant-to-Leader tasking.
- Inventory current instruction delivery paths across Tower, Leader, Ace, provider runtime, terminal/tmux, and API layers.
- Record the role boundaries in repo/project docs.
- Identify tests that already cover spawn/instruct/retry/reconnect and gaps that need scenario tests later.

**Exit criteria:**
- Local validation evidence exists.
- Current delivery/session paths are mapped.
- Docs explicitly preserve Operator → Tower → Leader → Ace boundaries.
- No behavior-changing refactor has started before baseline is clear.

**Phase artifact:** `docs/runtime_orchestration_phase0_baseline.md`

## Phase 1 — Delivery trace events and result vocabulary

**Goal:** Make instruction delivery observable without changing semantics much.

**Work:**
- Define a structured delivery trace model with fields such as:
  - `trace_id`, `session_id`, `role`, `provider`, `pane_id`, `action`, `stage`, `verdict`, `reason_code`, `timestamps`, `prompt_state_before`, `prompt_state_after`, `first_output_excerpt`.
- Add delivery stages such as:
  - `queued`
  - `write_started`
  - `written_to_pty`
  - `submit_attempted`
  - `prompt_cleared`
  - `agent_output_observed`
  - `confirmed_running`
  - `blocked`
  - `failed`
- Emit trace events from existing paths before extracting the shared runner.
- Keep the UI/API compatible at first; avoid breaking callers.

**Exit criteria:**
- Spawn/instruct attempts produce structured trace events.
- Failures include stable reason codes.
- Existing tests still pass.
- At least one workflow smoke test captures trace output for a Tower → Leader → Ace path.

## Phase 2 — Shared hardened session runner extraction

**Goal:** Move duplicated prompt/write/submit/verify behavior into one authoritative tmux-backed runtime runner.

**Work:**
- Extract a shared session runner responsible for:
  - pane/session inspection
  - prompt detection
  - atomic writes and Enter submission
  - verification reads
  - retry envelopes
  - provider-neutral delivery verdicts
- Keep provider-specific behavior behind provider adapters/configuration.
- Route one low-risk path through the runner first, then expand.
- Preserve existing external behavior while reducing duplicated logic.

**Exit criteria:**
- Shared runner exists behind current paths.
- At least one Leader or Ace instruction path uses the runner.
- No regression in current Tower/Leader/Ace smoke flow.
- Old duplicated code is marked for removal but not prematurely deleted if still needed for fallback.

## Phase 3 — Lifecycle state machines and transition guards

**Goal:** Replace ad hoc state mutation with explicit transition contracts.

**Work:**
- Define allowed transitions for:
  - session lifecycle states
  - Leader orchestration states
  - Ace execution states
  - task graph/task item states
- Add normalized transition error/reason codes.
- Prevent invalid transitions like retry paths jumping directly from `todo` to `in_progress` unless the model explicitly allows a bridge state.
- Keep transition failures structured and visible to Tower/Leader instead of silently stalling.

**Exit criteria:**
- Transition rules are documented and tested.
- Invalid transitions fail at one consistent boundary.
- Retry paths use explicit allowed transitions.
- Tower/Leader status surfaces blocked transition reasons.

## Phase 4 — Dialog/interruption pipeline

**Goal:** Centralize startup/trust/permission/welcome handling as runtime interrupts instead of scattered prompt hacks.

**Work:**
- Define runtime interrupt types, for example:
  - `trust_prompt`
  - `permission_prompt`
  - `login_required`
  - `welcome_screen`
  - `provider_error`
  - `unknown_prompt_blocker`
- Implement provider-specific detectors/resolvers for Claude/Codex where safe.
- Route interrupt detection through the shared runner.
- Require explicit escalation when an interrupt cannot be safely resolved.

**Exit criteria:**
- Startup/trust/permission screens are detected centrally.
- Trace events show interrupts and resolution attempts.
- Instructions are not reported as delivered while blocked by startup dialogs.
- Scenario tests cover at least one dialog/interruption path.

## Phase 5 — Migrate Tower/Leader/Ace operations to the runner

**Goal:** Make the shared runner the standard path for role operations while preserving the command hierarchy.

**Work:**
- Route Tower-start, Leader-spawn/instruct, Ace-spawn/instruct, stop/cancel, and inspect/reconnect through the shared runtime boundary.
- Ensure Tower communicates goals to Leaders and Leaders communicate tasks to Aces; do not introduce assistant/direct-to-Ace shortcuts as normal product flow.
- Update API/orchestration service surfaces to return stronger delivery/session results.
- Keep provider selection global where applicable and session-scoped where recorded.

**Exit criteria:**
- Tower, Leader, and Ace lifecycle/instruction paths use the shared runner or an explicit compatibility wrapper.
- API responses distinguish queued/delivered/blocked/failed states.
- Manual Playwright/local smoke test proves Tower-driven loop still works.

## Phase 6 — Reconcile and repair believed-vs-actual state

**Goal:** Detect and recover drift between DB/orchestration state and actual tmux/provider reality.

**Work:**
- Add reconciliation checks for:
  - DB active session but dead pane/process
  - live pane with missing/stale DB state
  - in-progress task with no bound live Ace
  - prompt present but no output progress
  - provider mismatch after settings changes
- Define repair actions:
  - mark stale
  - restart
  - reassign
  - escalate to Tower
  - require operator intervention
- Keep repair decisions auditable through trace/failure logs.

**Exit criteria:**
- Reconcile loop produces structured findings.
- Safe repairs are automated; unsafe repairs escalate.
- Stale active-session and orphaned-task scenarios have tests.

## Phase 7 — API/UX cleanup and operator truth surface

**Goal:** Expose the stronger truth model to operators without overwhelming the UI.

**Work:**
- Replace misleading `200 sent` style UX/API wording with precise states:
  - queued
  - delivered
  - verified running
  - blocked
  - failed
- Surface blocked reasons and recovery actions in Tower/Leader UI.
- Clean stale Claude-first startup/settings/auth copy so the Codex-default path is reflected accurately.
- Keep Tower as the primary operator-facing coordination surface.

**Exit criteria:**
- UI and API no longer imply success before verification.
- Operator can see whether work is queued, delivered, blocked, or actually progressing.
- Stale Claude-first messaging is removed or provider-conditional.

## Phase 8 — Scenario regression suite and cleanup

**Goal:** Lock in the workflow guarantees and remove old overlapping hacks only after the new path proves out.

**Work:**
- Add scenario tests for:
  - restart + restore
  - trust-dialog intercept
  - permission prompt intercept
  - Ace retry after partial failure
  - stale active-session recovery
  - retry-path task transitions
  - Tower-driven new project flow with Leader/Ace management
- Remove or simplify old recovery hacks once covered by shared runner/reconcile behavior.
- Update durable docs after behavior settles.

**Exit criteria:**
- Scenario suite covers the known regression cluster.
- Old duplicated recovery/delivery code is removed or clearly quarantined.
- Docs and code agree on runtime/orchestration responsibilities.

## Suggested PR sequence

1. **PR A:** Phase 0 validation notes + Phase 1 trace model skeleton.
2. **PR B:** Trace event emission in existing spawn/instruct paths.
3. **PR C:** Shared session runner skeleton and one migrated instruction path.
4. **PR D:** Runner migration for remaining Leader/Ace/Tower instruction paths.
5. **PR E:** Lifecycle transition guards and reason codes.
6. **PR F:** Dialog/interruption detector pipeline.
7. **PR G:** Reconcile loop and stale-session/task repair rules.
8. **PR H:** API/UX state vocabulary cleanup.
9. **PR I:** Scenario regression suite and removal of obsolete hacks.

## Immediate next action

Start with **Phase 0 → Phase 1**:

1. Run a fresh local ATC validation of Codex-default + global Tower Start.
2. Map the current delivery/session call paths.
3. Add the structured delivery trace model and reason-code vocabulary with minimal behavior change.

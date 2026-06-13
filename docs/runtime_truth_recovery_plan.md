# Runtime Truth and Recovery Plan

**Status:** Phases 1-9 implemented; follow-up Leader kickoff hardening planned in [`docs/leader_kickoff_recovery_plan.md`](leader_kickoff_recovery_plan.md)
**Last updated:** 2026-06-13
**Scope:** Provider-neutral runtime truth, delivery verification, health, and recovery for ATC Tower → Leader → Ace orchestration  
**Primary design constraint:** Provider-specific terminal behavior, including Codex update prompts and starter screens, must remain encapsulated inside provider adapters/classifiers. Tower, Leader, Ace, task graph, API, CLI, and UI layers may only depend on provider-neutral runtime truth.

## Purpose

ATC currently has several places where orchestration intent can look like execution truth: a Leader or Ace row can exist, a prompt can be visible in a pane, or a delivery call can return an optimistic `queued`/`sent` status while the provider runtime is actually idle, blocked behind an update/auth/trust prompt, stale after reload, or sitting at a default starter screen.

This plan turns that feedback into an implementation sequence that makes ATC explicitly aware of runtime truth without leaking Codex-specific prompt strategy into the product layers.

The goal is to make these statements provable through API, CLI, logs, and UI evidence:

- A Leader was started and actually accepted the kickoff goal.
- Leader kickoff verification distinguishes session creation, provider readiness, payload write, submit, goal acceptance, first actionable step, and task graph creation; see [`docs/leader_kickoff_recovery_plan.md`](leader_kickoff_recovery_plan.md) for the follow-up phases.
- A Leader began work or created project tasks before Tower backs off to normal monitoring.
- An Ace assignment was delivered, submitted, accepted, and moved past default prompt state before a task is treated as truly working.
- Runtime interruptions such as update prompts, auth prompts, missing panes, stale sessions, provider errors, and unsubmitted prompts become explicit blockers with safe recovery recommendations.
- Recovery can restart a stale Leader or re-dispatch an Ace task from persisted source-of-truth inputs instead of relying on Tower to reconstruct the original goal/task manually.

## Non-negotiable boundaries

### Role responsibility split

Normal product flow remains:

```text
Operator / assistant → Tower → Leader → Ace
```

- **Tower** creates projects, starts Leaders, verifies Leader kickoff, monitors Leader/project health at low frequency, and reports blockers/completion upward.
- **Leader** decomposes the project goal, creates task graphs, starts/manages Aces, monitors Ace progress, recovers stuck Ace dispatch where safe, and reports project status upward.
- **Ace** executes one assigned task, reports done/blocked, and avoids cross-task ownership unless explicitly instructed by Leader.

Tower may inspect Ace truth only when Leader reports blocked, Leader runtime is missing, progress is flat past a longer threshold, or the operator asks for detailed status.

### Provider encapsulation

Product layers must not hard-code provider prompt text or recovery mechanics.

Allowed in orchestration/API/UI:

- `runtime_state`
- `delivery_state`
- `blocker_reason`
- `last_activity_at`
- `kickoff_verified`
- `dispatch_verified`
- `recovery_recommendation`
- redacted provider diagnostics for display/debugging

Not allowed outside provider adapters/classifiers:

- Matching exact Codex update prompt text.
- Assuming Codex reload/exit behavior after an update.
- Sending provider-specific key sequences directly from Tower/Leader/Ace code.
- Treating Codex starter prompt labels as first-class domain states.

Provider-specific classifiers map observations into provider-neutral facts. Example:

```json
{
  "runtime_state": "blocked",
  "delivery_state": "blocked",
  "blocker_reason": "runtime_update_required",
  "provider": "codex",
  "provider_diagnostics": {
    "provider_reason": "codex_update_prompt",
    "redacted_excerpt": "A new version of Codex is available..."
  }
}
```

Tower reacts to `runtime_update_required`, not to `codex_update_prompt` directly.

## Conceptual model

Avoid one giant state enum that mixes runtime existence, delivery progress, activity, blockers, and recovery. Track them as separate but correlated dimensions.

### Runtime state

| State | Meaning |
|---|---|
| `missing` | No live runtime/pane/process can be found for the session. |
| `starting` | Runtime spawn has begun but readiness is not verified. |
| `ready` | Runtime is attachable and provider appears ready for input. |
| `active` | Provider accepted work and visible output/activity or task progress exists. |
| `idle` | Runtime exists but no current work/activity is observed. |
| `idle_at_default_prompt` | Runtime shows provider starter/default prompt and has not accepted ATC work. |
| `blocked` | Runtime exists but is interrupted by a classified blocker. |
| `stale` | Runtime should not be reused, e.g. after provider update/reload/exit. |
| `complete` | Work finished normally. |
| `failed` | Runtime failed unexpectedly. |

### Delivery state

| State | Meaning |
|---|---|
| `not_started` | No delivery attempt exists. |
| `queued_unverified` | ATC queued the attempt but has no runtime proof yet. |
| `runtime_created` | Session/runtime row exists and spawn was requested or completed. |
| `prompt_visible` | Payload text is visible but submission is not proven. |
| `payload_written` | Payload write to PTY was accepted by tmux/runtime substrate. |
| `submit_sent` | Enter/submit was sent as part of the delivery attempt. |
| `submitted_pending_acceptance` | Submission happened but provider acceptance/activity is not yet observed. |
| `accepted_active` | Provider accepted the prompt and began work or produced a valid signal. |
| `blocked` | Delivery is blocked by a classified runtime/provider condition. |
| `failed` | Delivery failed. |

### Blocker reasons

Stable provider-neutral reason codes should include at least:

- `pane_missing`
- `runtime_update_required`
- `runtime_auth_required`
- `runtime_trust_required`
- `runtime_permission_required`
- `default_prompt_visible`
- `prompt_not_submitted`
- `provider_error`
- `tool_server_error`
- `leader_no_activity`
- `ace_dispatch_failed`
- `stale_after_update`
- `unknown_prompt_blocker`

Provider adapters may attach provider-specific diagnostics under a nested field, but product logic should branch on the stable reason code.

### Recovery state

| State | Meaning |
|---|---|
| `not_needed` | No recovery needed. |
| `queued` | Recovery requested. |
| `inspecting` | Runtime and persisted state are being reconciled. |
| `runtime_update_required` | Provider update prompt or equivalent runtime update blocker detected. |
| `updating` | Safe update action is in progress under policy. |
| `restart_required` | Current runtime should be considered stale and replaced. |
| `restarting` | Old runtime is stopping and a fresh one is starting. |
| `kickoff_resending` | Persisted Leader goal is being resent to a fresh runtime. |
| `dispatch_resending` | Persisted Ace task assignment is being resent. |
| `verifying` | Kickoff/dispatch acceptance is being verified. |
| `recovered` | Recovery completed and verification passed. |
| `blocked` | Recovery cannot proceed without operator/provider action. |
| `failed` | Recovery attempted and failed. |

## Recovery policy

Recovery should be policy-driven and inspect-first.

Suggested policy values:

| Policy | Behavior |
|---|---|
| `block_only` | Detect and classify only; never mutate runtime. |
| `notify_operator` | Detect, classify, and return operator action guidance. |
| `auto_accept_safe_prompts` | Auto-answer only explicitly safe provider prompts. |
| `auto_accept_updates_and_restart` | Accept safe provider update prompts, mark current runtime stale, restart, and verify with persisted kickoff/dispatch payload. |

Default behavior should be conservative: inspect, classify, surface blocker, and recommend recovery. Fully automatic update acceptance should be opt-in or guarded by project/global config.

## Phase 1 — Provider-neutral runtime truth schema and evidence plumbing

**Goal:** Add the shared vocabulary and evidence path that all later phases use. This phase should not deeply alter runtime behavior yet.

### Work

1. Define provider-neutral enums/data models for:
   - `RuntimeState`
   - `DeliveryState`
   - `BlockerReason`
   - `RecoveryState`
   - `RecoveryRecommendation`
   - `RuntimeInspection`
   - `DeliveryVerdict`
2. Add additive persistence/event metadata for runtime truth:
   - `runtime_state`
   - `delivery_state`
   - `blocker_reason`
   - `last_activity_at`
   - `last_inspected_at`
   - `provider`
   - `provider_diagnostics` with redaction rules
3. Ensure trace events can correlate a single spawn/delivery/recovery attempt with:
   - project id
   - role/session id
   - task id when applicable
   - trace id
   - provider
   - pane id when available
   - stage/verdict/reason
4. Keep existing API responses backward-compatible while adding explicit truth fields.
5. Add unit tests for enum serialization, reason-code stability, redaction, and trace construction.

### Acceptance criteria

- Runtime/delivery/blocker/recovery types exist in a provider-neutral module.
- Existing callers can keep working without interpreting the new fields.
- Trace/event metadata can represent `queued_unverified`, `prompt_visible`, `submit_sent`, `accepted_active`, `blocked`, and `failed` distinctly.
- Redacted diagnostics are safe to show in logs/API evidence.
- Targeted unit tests pass.

### Validation

- Run targeted backend tests for runtime model/schema helpers.
- Run at least one smoke path that emits a trace for a Tower → Leader kickoff attempt without requiring the new recovery behavior yet.
- Capture API/event output showing the new fields.

## Phase 2 — Provider-owned runtime classifiers

**Goal:** Move prompt/pane interpretation behind provider adapters and map provider-specific observations into the Phase 1 neutral schema.

### Work

1. Define provider classifier interface, for example:

   ```python
   class RuntimeProviderClassifier(Protocol):
       async def inspect_runtime(...) -> RuntimeInspection: ...
       def classify_excerpt(...) -> RuntimeClassification: ...
       def recovery_capabilities(...) -> RecoveryCapabilities: ...
   ```

2. Implement Codex classifier mappings for:
   - update prompt → `runtime_update_required`
   - post-update/reload/exit state → `stale_after_update` / `stale`
   - default starter prompt → `idle_at_default_prompt` / `default_prompt_visible`
   - visible but unsubmitted text → `prompt_not_submitted`
   - active reasoning/output → `active` / `accepted_active`
   - provider errors → `provider_error`
   - auth/trust/permission gates → neutral auth/trust/permission blockers
3. Keep exact Codex string matching in the Codex adapter/classifier only.
4. Add compatibility classifiers or explicit unimplemented/fallback behavior for other providers so they return `unknown_prompt_blocker` or `submitted_pending_acceptance` rather than lying.
5. Add mocked pane/excerpt tests per classification.

### Acceptance criteria

- No Tower/Leader/Ace code matches Codex prompt strings.
- Codex-specific prompt observations map to provider-neutral states/reasons.
- Unknown provider prompt states produce conservative blockers or uncertain states, not success.
- Tests cover update prompt, default prompt, unsubmitted text, active output, missing pane, provider error, and auth/trust/permission blockers.

### Validation

- Unit tests with mocked pane excerpts.
- Static search confirms product/orchestration layers do not contain Codex prompt-text branching.
- API inspection output includes neutral fields plus nested provider diagnostics.

## Phase 3 — Strong Leader startup and kickoff verification

**Goal:** `atc leader start --goal ...` should return a strong delivery verdict and Tower should not enter normal monitoring until Leader kickoff is verified.

### Work

1. Persist the Leader original goal/kickoff payload as source-of-truth project/session data.
2. Update Leader start flow to report:
   - runtime created
   - payload written
   - submit sent
   - provider accepted or not
   - Leader began work or not
3. Define valid kickoff signals:
   - provider active output/reasoning after submission
   - task graph created/progress API updated
   - Leader status event emitted from runtime hooks
4. Define invalid signals:
   - default provider prompt still visible
   - starter suggestions still visible
   - payload text visible but not submitted
   - runtime row exists without active evidence
5. Add startup verification window and clear failure/blocker outputs.
6. Update Tower startup behavior so normal monitoring begins only after `kickoff_verified = true` or a classified blocker is reported.

### Acceptance criteria

- `atc leader start --goal ...` distinguishes `queued_unverified`, `prompt_visible`, `submit_sent`, `submitted_pending_acceptance`, `accepted_active`, `blocked`, and `failed`.
- Leader original goal is recoverable from ATC state.
- Tower reports startup blockers instead of treating unverified kickoff as usable.
- Tests cover accepted kickoff, unsubmitted prompt, default prompt, update blocker, and missing pane.

### Validation

- CLI/API output from a successful kickoff shows `kickoff_verified: true`.
- CLI/API output from a mocked blocked kickoff shows the reason and recommended recovery.
- Playwright/API evidence shows Tower waiting for verified Leader kickoff before backing off.

## Phase 4 — Ace dispatch verification and repair contract

**Implementation status:** Complete in PR #290. ATC now persists Ace assignment
dispatch truth separately from assignment/task graph lifecycle and only promotes
task work to `working`/`in_progress` after provider-neutral accepted/active
runtime evidence. Full repair commands remain Phase 5/6 work.

**Goal:** A task is not truly `working` until its assigned Ace runtime received, submitted, and accepted the task assignment or is explicitly blocked with a reason.

### Work

1. Apply the same delivery truth contract to Leader → Ace dispatch.
2. Track Ace-level fields:
   - `dispatch_delivery_state`
   - `dispatch_verified`
   - `last_activity_at`
   - `assigned_task_id`
   - `blocker_reason`
3. Prevent task graph `working`/`in_progress` from meaning merely assigned.
4. Add provider classifier checks for idle/default Ace prompts.
5. Add repair path design for stuck Ace dispatch:
   - inspect Ace runtime
   - determine whether payload is unsubmitted, stale, blocked, or missing
   - re-submit/re-dispatch only when safe and idempotent
   - otherwise escalate to Leader/Tower
6. Preserve one active Ace per task; reuse or classify existing live Ace before spawning duplicates.

### Acceptance criteria

- Ace assignment state and runtime state are distinct.
- Task `working` means actual Ace runtime acceptance/activity or a separately labeled blocked state.
- Idle/default Ace prompt is not treated as active work.
- Leader owns normal Ace recovery; Tower only inspects Ace panes under escalation conditions.
- Tests cover stuck dispatch, default prompt, missing pane, active Ace, stale Ace, and duplicate-Ace prevention.

### Validation

- Scenario/API test shows a Leader-created Ace task reaches `dispatch_verified: true` before task is considered working.
- Blocked dispatch surfaces `ace_dispatch_failed` or a more specific provider-neutral blocker.
- UI evidence shows task state and runtime state separately.

## Phase 5 — Health commands and inspect-first recovery CLI

**Implementation status:** Implemented in PR #291. Added provider-neutral `RuntimeHealth` and recovery planning service, REST endpoints for Leader/Ace health and recovery dry-run/apply plans, CLI wrappers, project-owned Ace scoping guards, and tests for health shape, cross-project Ace safety, and inspect-first recovery semantics.

**Goal:** Add human/operator commands that summarize runtime truth and offer safe recovery paths.

### Work

1. Add health commands:
   - `atc leader health --project-id <id>`
   - `atc ace health --project-id <id> --ace-id <id>`
2. Health output should summarize:
   - runtime exists
   - pane attached
   - provider
   - runtime state
   - delivery/kickoff/dispatch state
   - update/auth/trust/permission/default prompt blocker presence
   - task graph state
   - last activity timestamps
   - Ace count and dispatch summary
   - current blocker
   - recommended recovery command
3. Add inspect-first recovery commands:
   - `atc leader recover --project-id <id> [--dry-run|--apply] [--policy <policy>]`
   - `atc ace recover --project-id <id> --ace-id <id> [--dry-run|--apply] [--policy <policy>]`
4. Default recovery should inspect/report unless policy and safety classification allow mutation.
5. For Leader recovery, use persisted original kickoff goal.
6. For Ace recovery, use persisted assignment/task payload from Leader-owned task state.

### Acceptance criteria

- Health commands work without provider-specific CLI flags.
- Health output is provider-neutral at top level and includes nested redacted diagnostics.
- Recovery dry-run shows planned actions without mutation.
- Recovery apply refuses unsafe states unless operator action is required and explicit.
- `pane_missing`/`runtime_update_required` errors include concrete recovery guidance.

### Validation

- CLI tests for health output shape.
- Recovery dry-run tests for missing pane, update required, default prompt, stale runtime, and accepted-active state.
- Manual CLI/API evidence for a safe mocked recovery path.

## Phase 6 — Update/stale-session recovery implementation

**Goal:** Implement provider-policy-driven recovery for runtime update prompts and stale post-update sessions while keeping mechanics provider-owned.

**Implemented in PR #292:**

- Added provider-capability-aware recovery planning for runtime update prompts.
- Added explicit recovery policies (`inspect_first`, `restart_missing_pane`, `restart_stale_runtime`, `auto_accept_updates_and_restart`) instead of optimistic apply behavior.
- Kept provider update mechanics encapsulated: update prompt acceptance is refused until a provider adapter exposes a safe mutating capability.
- Added stale/missing Leader apply recovery that restarts from persisted Leader goal.
- Kept Ace recovery Leader-owned: direct Ace apply is refused so dispatch/re-dispatch stays under Leader task ownership.
- Added tests covering update prompt policy gating, provider capability checks, stale Leader goal reuse, and existing health/recovery regressions.

### Work

1. In provider adapters, expose recovery capability metadata:
   - can detect update prompt
   - can safely accept update prompt
   - requires fresh session after update
   - expected post-update behavior
2. Implement Codex update handling inside the Codex adapter only:
   - detect update prompt
   - if policy permits, accept update prompt
   - observe exit/reload/stale state
   - mark old session `stale`
   - request orchestration to stop old runtime
   - start fresh Leader/Ace runtime as appropriate
   - resend persisted kickoff/dispatch payload
   - verify kickoff/dispatch
3. Ensure the central recovery service drives the neutral flow and does not contain Codex prompt strings.
4. Add audit events for every recovery transition.

### Acceptance criteria

- Codex update prompt is detected as `runtime_update_required` with provider diagnostics.
- Auto-update behavior is policy-gated.
- Post-update sessions are not reused when provider says they are stale.
- Fresh runtime restart uses persisted original goal/task payload.
- Tests prove provider-specific mechanics remain encapsulated.

### Validation

- Mocked Codex update-prompt recovery test: blocked-only, dry-run, and apply.
- Recovery evidence shows old session stale, fresh session started, kickoff/dispatch resent, and verification passed or blocked.
- Search confirms Codex update prompt matching is limited to Codex provider code/tests.

## Phase 7 — Front-door contract consistency

**Goal:** Keep REST API, `atc` CLI, and `atc-mcp` as thin, purpose-specific, non-equal adapters over the same orchestration/runtime truth contract.

ATC does not need three independent communication systems. It needs one canonical service contract with three front doors that serve different audiences:

- **REST API:** canonical product/backend interface for UI, system integrations, health, recovery, progress, task graph, and session state.
- **`atc` CLI:** agent/operator convenience wrapper for terminal contexts, status reporting, done/blocked updates, health/recovery commands, and scripted workflows.
- **`atc-mcp`:** optional external adapter for MCP-capable clients; it should bridge into `OrchestrationService`, not define separate orchestration behavior.

### Work

**Implemented in Phase 7:**

- Aligned orchestration response semantics around truthful front-door states: `queued`, `submitted`, `confirmed`, `blocked`, and `failed`.
- Changed orchestration operation defaults from optimistic `accepted` to `queued`.
- Made Leader spawn via orchestration report `queued` with recovery guidance instead of implying the Leader acted on the goal.
- Made Ace spawn and send-instruction surfaces report `submitted`, `blocked`, or `failed` based on provider-neutral delivery results, without claiming provider acknowledgement.
- Kept REST as the canonical contract while exposing the same structured response fields through CLI JSON output and MCP tool payloads.
- Updated MCP tool descriptions so external clients understand `queued`/`submitted` are not provider acceptance.
- Added/updated REST/MCP/orchestration tests for truthful delivery states and response shape.

1. Document the canonical control path:
   - core services own runtime/orchestration/recovery behavior;
   - REST, CLI, and MCP are adapters;
   - no adapter owns provider-specific recovery logic or independent task/session semantics.
2. Align all three surfaces around the same provider-neutral fields:
   - `runtime_state`
   - `delivery_state` / `truth_delivery_state`
   - `blocker_reason`
   - `last_activity_at`
   - `last_inspected_at`
   - `provider_diagnostics`
   - `recovery_recommendation`
3. Standardize response wording so no front door reports optimistic success when truth is only queued/unverified.
4. Prefer `atc` CLI in agent-facing role instructions where it improves consistency and ergonomics, while keeping REST as the product API and MCP as an optional external bridge.
5. Add contract tests that compare REST/CLI/MCP output shape for equivalent operations.
6. Ensure health and recovery commands are implemented once in services and exposed through all applicable adapters.

### Acceptance criteria

- REST, CLI, and MCP expose consistent runtime truth for the same session/operation.
- CLI/MCP wrappers do not fork recovery logic or provider-specific prompt handling.
- Agent-facing docs clearly state when to use CLI versus REST and do not imply separate sources of truth.
- MCP remains optional; Tower/Leader/Ace execution does not depend on MCP unless explicitly configured.
- Tests cover adapter parity for health, delivery status, blocker reporting, and recovery dry-run output.

### Validation

- REST/API tests verify canonical response shape.
- CLI tests verify equivalent output and exit behavior for healthy, blocked, and unverified states.
- MCP tool tests verify equivalent structured payloads for session health/delivery/recovery operations.
- Docs/search verification confirms provider-specific Codex handling remains in provider adapters/classifiers, not in REST/CLI/MCP front doors.

## Phase 8 — Monitoring cadence and responsibility enforcement

**Goal:** Make Tower quieter and make Leader responsible for normal Ace monitoring once Leader health is verified.

### Work

1. Add Tower monitoring phases:
   - startup/kickoff verification: active for first ~2 minutes or until verified/blocked
   - healthy Leader: poll every 3–5 minutes
   - visible Leader activity: back off further
   - no Leader-visible activity for 5–10 minutes: inspect/nudge
2. Make Tower avoid Ace pane inspection unless:
   - Leader reports blocked
   - Leader runtime missing
   - project progress flat beyond threshold
   - user asks for detailed status
3. Ensure Leader monitoring loop owns Ace dispatch/progress recovery.
4. Add `last_activity_at` updates for Leader, Ace, and task progress.
5. Add operator-facing status summaries that cite runtime truth instead of guesswork.

### Acceptance criteria

- Tower cadence changes after `kickoff_verified: true`.
- Tower does not inspect Ace panes during healthy Leader-owned execution.
- Leader-owned Ace recovery is exercised by tests or scenario smoke.
- Last activity timestamps drive no-activity decisions.

### Validation

- Unit/integration tests for cadence policy.
- Scenario test showing Tower startup verification then backoff.
- Evidence that Ace inspection is skipped while Leader is healthy and active.

### Phase 8 implementation notes

- `atc.tower.monitoring.decide_tower_monitoring_cadence` centralizes the
  provider-neutral Tower cadence policy.
- Tower startup verification actively checks Leader health during the startup
  window, then backs off once Leader output/activity or task progress proves the
  Leader is healthy.
- Tower escalation to Ace detail is limited to explicit blockers, missing Leader
  runtime, flat project progress past threshold, or operator-requested detail.
- `LeaderOrchestrator.monitor_ace_assignments` keeps Ace assignment health and
  blocker reporting inside the Leader-owned task flow.
- Tests cover healthy Leader backoff without Ace inspection, flat-progress
  escalation, Leader-owned Ace blocker reporting, and `last_activity_at`-driven
  decisions.

## Phase 9 — API/UI truth surfacing and docs cleanup

**Goal:** Surface the truth model in the API/UI without overwhelming operators, and remove/rename optimistic wording.

### Work

1. Update progress API so task state and runtime state are separate.
2. Replace optimistic statuses with explicit uncertainty:
   - `queued_unverified`
   - `delivered_unsubmitted`
   - `submitted_pending_acceptance`
   - `accepted_active`
   - `blocked`
3. Add UI labels/tooltips for runtime/delivery/blocker state.
4. Add event/failure-log views or links for runtime incidents.
5. Update docs and architecture references with final contracts.
6. Remove stale compatibility paths once tests and evidence show the new path covers them.

### Acceptance criteria

- UI no longer implies task work when only assignment/delivery intent exists.
- API docs describe task state vs runtime state vs delivery state.
- Operator can see a concise health/blocker summary and drill into evidence.
- Old optimistic language is removed or clearly marked legacy.

### Validation

- API tests for response shape and backwards-compatible fields.
- Playwright screenshots showing healthy, blocked, and uncertain states.
- Baseline smoke path verifies no regression in dashboard/project/Tower/Leader/Ace flows.

### Implementation status

Implemented in Phase 9:

- Task graph API responses expose additive provider-neutral `task_state`, `runtime_state`, `delivery_state`, `assignment_status`, `dispatch_verified`, `blocker_reason`, `last_activity_at`, and nested `runtime_truth` fields while preserving legacy `status` compatibility.
- Tower progress keeps legacy task lifecycle counters and adds `task_states`, `runtime_states`, `delivery_states`, `blocked`, and `dispatch_unverified` summaries.
- Leader Task Board UI separates task state from runtime truth, adds an `Assigned` column, and surfaces verified/unverified/blocker evidence without provider-specific prompt matching.
- API docs describe task/planning state vs runtime/delivery truth and mark legacy optimistic status semantics explicitly.

## Required full-loop validation for every phase

Each phase must complete the established ATC loop:

1. branch from current `main`;
2. implement the phase slice;
3. run targeted backend/frontend tests;
4. run changed-behavior validation;
5. open PR;
6. review diff and runtime-boundary risks;
7. merge;
8. rerun post-merge validation from `main`;
9. run baseline Mac Studio Playwright smoke path;
10. post screenshots/reports on the PR when a PR exists;
11. reconcile docs/status before moving to the next phase.

For phases touching Tower/Leader/Ace orchestration, runtime delivery, task assignment, recovery, or progress reporting, validation must include Ace execution-truth evidence:

- Tower drives Leader startup.
- Leader creates/reuses a live Ace for a task.
- The task graph assignment points to that live `ace` session.
- Runtime evidence shows the Ace received and submitted the assignment, then became active or blocked with a classified reason.
- `202 Accepted`, `sent`, a session row, or visible prompt text alone is not sufficient proof.

## Initial Phase 1 implementation target

Phase 1 should start with the smallest provider-neutral slice:

1. Add runtime truth enums/models and redaction helpers.
2. Add trace/event construction helpers that can represent existing delivery attempts without changing behavior.
3. Thread additive fields through one low-risk service/API path.
4. Add focused tests for model serialization and trace shape.
5. Capture one smoke trace from a Leader kickoff attempt.

Phase 1 should not yet auto-recover Codex updates, change Tower cadence, or reclassify Ace task state. Those become safer once the neutral truth model exists.

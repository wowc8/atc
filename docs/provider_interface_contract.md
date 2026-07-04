# Provider Interface Contract

This document defines the provider-neutral contracts that CLI-backed providers must adapt into. It is intentionally stricter than an implementation guide: provider modules may vary internally, but shared ATC interfaces must stay stable and provider-neutral.

## Contract rule: adapt providers, do not reshape ATC

New providers must adapt provider-specific behavior into existing ATC interfaces. Do not modify shared orchestration, session, terminal, token, or API contracts inside a provider onboarding PR unless there is a separate architecture decision proving a generic need.

A shared interface change requires:

- a design log explaining why the existing interface is insufficient;
- at least one non-current-provider use case or generic ATC benefit;
- compatibility tests for existing providers;
- updates to this contract;
- explicit PR review focus on the shared change.

## Provider-owned responsibilities

Provider modules own all provider-specific behavior:

| Responsibility | Provider-owned examples |
| --- | --- |
| CLI discovery | binary name, version command, PATH quirks, launch flags |
| Auth/trust | first-run prompt text, login status, trust prompts, blocked-state parsing |
| Session metadata | external session IDs, working directory conventions, provider run IDs |
| Terminal interpretation | provider-specific readiness text, prompt boundaries, completion markers |
| Token telemetry | file paths, event names, JSON shapes, cumulative total semantics |
| Health/error classification | provider-specific retryable/fatal/blocked messages |

Provider modules convert those details into normalized ATC facts.

## Shared interface boundaries

Shared code may depend only on provider-neutral values:

| Shared layer | Allowed inputs | Forbidden provider-specific inputs |
| --- | --- | --- |
| Orchestration | ATC project/session/task IDs, role, state, events | provider CLI output strings, provider prompt text |
| Session lifecycle | session ID, command/env, PTY handle, readiness state | provider-specific metadata parsing outside provider module |
| Terminal | PTY bytes, resize events, scroll/follow state | provider trust prompt wording or token telemetry formats |
| Token tracking | `TokenUsageIncrement`, provider name, source IDs | provider files, JSONL schemas, event names, pricing/cost data |
| API/UI | normalized status, token summaries, health fields | hidden provider parsing logic in UI/shared routers |

## Runtime/session interface

Every provider must expose enough behavior for ATC to manage sessions through the existing lifecycle.

Required normalized facts:

- provider name;
- executable command and environment;
- ATC session ID;
- optional external provider session ID;
- working directory/project mapping;
- role: Tower, Leader, Ace, or provider-neutral runtime role;
- readiness state;
- blocked reason, if blocked;
- recovery guidance, if recoverable;
- health timestamp and status.

The provider module may use provider-specific classes internally, but the shared session lifecycle should receive stable, provider-neutral primitives.

## Orchestration interface

Providers must support ATC's command chain:

```text
Operator → Tower → Leader → Ace
```

Shared orchestration contracts:

- Tower receives operator goals and coordinates Leaders.
- Leader receives project context, plans work, spawns Aces, assigns tasks, and monitors completion.
- Ace receives task-scoped instructions and reports execution state/artifacts.
- Event bus and DB records are the source of truth for orchestration state.

Provider modules must not add alternate paths that let a provider bypass the Tower/Leader/Ace chain for normal work.

## Terminal interface

The terminal layer is provider-neutral. It manages PTY/tmux streaming, resizing, and UI delivery.

Provider modules may provide:

- command argv/env;
- launch cwd;
- readiness detector;
- blocked-state detector;
- provider-specific parser callbacks if wired through a provider-owned boundary.

Shared terminal code must not encode provider prompt strings, auth text, token event formats, or provider filesystem paths.

## Prompt submission interface

Prompt dispatch must preserve ATC's atomic-send invariant:

- instruction text and Enter are sent without an await gap;
- prompt dispatch is associated with an ATC session/task when applicable;
- provider readiness/trust state is checked before dispatch when possible;
- failures are structured and recoverable.

Provider-specific prompt wrappers or slash commands belong under the provider module.

## Token usage interface

Shared token tracking accepts only normalized increments:

```python
TokenUsageIncrement(
    session_id=str,
    project_id=str | None,
    provider=str,
    model=str | None,
    recorded_at=datetime,
    input_tokens=int,
    cached_input_tokens=int,
    output_tokens=int,
    reasoning_output_tokens=int,
    total_tokens=int,
    source=str,
    external_session_id=str | None,
    source_event_id=str | None,
    raw_usage=dict | None,
)
```

Provider modules own:

- telemetry source discovery;
- parser fixtures;
- event-shape handling;
- source key semantics;
- high-water/de-dupe offsets where provider-specific;
- cumulative total to increment conversion;
- external session mapping.

Shared token tracking owns:

- validation of normalized increments;
- append-only usage event persistence;
- aggregation/summaries;
- fanout to events/WebSockets;
- token limit enforcement;
- provider-neutral high-water primitives when useful.

Shared token tracking must not know about provider-specific files, event names, or token payload shapes.

## Runtime truth and recovery interface

Providers must normalize their runtime truth into states ATC can display and act on:

- `available` / `unavailable`;
- `starting` / `ready` / `running` / `blocked` / `failed` / `completed` where applicable;
- blocked reason code;
- operator-readable recovery guidance;
- last checked timestamp;
- last sync timestamp for usage collectors;
- structured failure log fields.

If provider output, DB state, terminal state, or API state disagree, the provider integration must surface the mismatch rather than hiding it behind a healthy status.

## Context, artifact, and report interface

Provider modules must use ATC context and artifact channels:

- Leaders own context packaging.
- Aces receive assignment context through Leader-managed paths.
- Aces report progress/artifacts through ATC report/event APIs.
- Provider-specific artifact discovery may happen in provider code, but artifacts become normalized paths/events before reaching shared UI/API surfaces.

## Boundary regression expectations

Every provider PR must include a provider-boundary scan tailored to the provider. Examples:

```text
# Codex-specific examples that must not appear in shared token tracking
.codex
token_count
rollout-
codex_jsonl
```

General scan targets:

- shared token tracking modules;
- shared orchestration modules;
- shared terminal modules;
- shared API models;
- frontend shared state/components.

A provider term in a shared module is acceptable only when it is intentionally provider-neutral metadata, such as a provider name passed through a generic field, and the PR explains why.

## Cost and billing prohibition

ATC provider integrations are token-only. Do not add provider pricing, cost estimates, billing limits, dollar budgets, or cost endpoints as part of provider onboarding.

Forbidden examples:

```text
cost_usd
monthly_cost_limit
today_cost
month_cost
/api/usage/cost
/api/tower/cost
CostModel
cost_model
input_cost_per_token
output_cost_per_token
CostTracker
```

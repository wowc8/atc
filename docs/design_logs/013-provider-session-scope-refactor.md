# 013 — provider-session-scope-refactor

**Date**: 2026-05-28
**Status**: accepted

## Decision

ATC will move toward a global provider model and an explicit session scope model. The selected provider in settings becomes the runtime source of truth for new session creation and restart decisions. Sessions become provider-bound at birth, and provider changes replace mismatched sessions instead of mutating them in place. Tower is modeled as a global-scoped session rather than being anchored to a real or sentinel project. Leader and Ace sessions remain project-scoped. The migration will be staged: first add explicit provider and scope fields to sessions plus global-provider-aware restart logic, then migrate Tower off fake project anchoring, then introduce a generic context ownership model for durable context storage.

## Context

Recent provider-switch work exposed two design problems.

First, provider semantics are currently split awkwardly between a global settings default and per-project `agent_provider` fields. That led to confusing UX, such as applying provider changes to one selected project while Tower appeared global. Matthew explicitly rejected that behavior and wants provider switching to be lightweight and global: current sessions should be restarted or replaced as needed, and future create or restart flows should always consult the currently selected provider.

Second, Tower is conceptually global but is still persisted as if it were project-scoped because the `sessions` table requires `project_id`. To satisfy that schema, Tower has been anchored either to the first active project or to a fake `Tower Workspace` sentinel project. That coupling leaked into restore and restart behavior, including a live bug where Tower restart reused an old session with the wrong project context and left the UI in a misleading state. Matthew explicitly called out the project attachment as a bad design smell and proposed separating project/session/provider concerns more cleanly.

A full one-shot removal of `sessions.project_id` across the entire system would create too much migration risk because Leader and Ace sessions are still genuinely project-scoped. A staged scope model is safer.

## Invariants

- The selected provider in settings is the global runtime source of truth.
- Sessions are provider-bound at birth.
- Provider changes do not mutate live sessions in place.
- Session create and restart flows must always consult the current provider.
- A session may be reused only when both scope and provider match.
- A provider mismatch requires replacement, not reuse.
- Tower is global-scoped.
- Leader and Ace sessions remain project-scoped.
- Durable context survives replacement; raw terminal continuity does not.

## Proposed schema direction

### Phase 1: lightweight lifecycle support

Add the following fields to `sessions`:

- `provider TEXT NOT NULL` — the provider used when the session was created
- `scope_type TEXT NOT NULL` — `global` or `project`
- `scope_id TEXT NULL` — nullable identifier for the scope owner

Interpretation:

- Tower session → `scope_type='global'`, `scope_id=NULL`
- Leader/Ace session → `scope_type='project'`, `scope_id=<project_id>`

Keep `sessions.project_id` temporarily for compatibility during the migration. Existing code can be moved over incrementally instead of forcing a single high-risk schema cut.

### Phase 2: generic durable context ownership

Introduce a generic context table, for example:

- `context_entries`
  - `id`
  - `context_type TEXT NOT NULL` — `global`, `project`, `task`, `session`
  - `context_id TEXT NULL`
  - `kind TEXT NOT NULL` — e.g. `md`, `summary`, `memory`, `config`
  - `key TEXT NOT NULL`
  - `content TEXT NOT NULL`
  - `created_at`
  - `updated_at`

Use `context_type='global'` and `context_id=NULL` for global entries.

This avoids encoding global-vs-project meaning through fake projects or arbitrary nullable project columns.

### Phase 3: cleanup

Once Tower and restore/restart paths are fully off project anchoring, legacy Tower reliance on `sessions.project_id` can be removed. Later, broader `project_id` usage can be narrowed or deleted where it is only a historical artifact rather than true domain ownership.

## Provider switching lifecycle

The lightweight provider-switch model is:

1. Update the global provider in settings.
2. Enumerate live sessions.
3. For each live session:
   - if `session.provider == current_provider`, it may continue or be reused on restart
   - if `session.provider != current_provider`, mark it stale-for-provider and replace it on restart or immediately if needed
4. Restart or replace Tower under the new provider.
5. Restart or replace Leader/Ace sessions under the new provider as needed.
6. Any future create or restart flow always checks the current provider before deciding to reuse a session.

Key rule:

- matching provider + matching scope → reuse is allowed
- mismatched provider or scope → create a new session

This keeps the provider switch lightweight and avoids trying to mutate a live Claude/Codex/OpenCode process into another provider identity.

## Replacement / handoff behavior

When a mismatched session is replaced:

- stop or archive the old session
- create a new session with the current provider and correct scope
- rehydrate from durable context, such as:
  - project context and repo metadata
  - current leader goal or task graph
  - global or project markdown context
  - optional generated handoff summary from the replaced session

Replacement should not pretend to preserve raw terminal continuity. It is a new session with inherited durable context.

## API and UI implications

### Backend

- settings provider becomes the effective runtime source of truth
- session create/restart/reconnect flows need provider-match checks
- Tower start/restore logic should use global scope rather than sentinel project anchoring
- restore logic must reject stale provider-mismatched sessions as canonical live sessions

### Frontend

- remove the selected-project provider apply UX
- present provider selection as global
- explain that existing incompatible sessions will be restarted or recreated
- Tower should show the current global provider and stale-for-provider state where relevant

### Project model compatibility

`projects.agent_provider` can remain temporarily as a compatibility field or mirrored value during rollout. Long-term, the product direction is to stop treating per-project provider as the source of truth unless explicit per-project overrides are reintroduced as a deliberate product feature.

## Rollout phases

### Phase 1

- add `sessions.provider`
- add `sessions.scope_type` and `sessions.scope_id`
- make create/restart/reconnect provider-aware
- make provider switching global in settings
- remove selected-project provider-apply UX

### Phase 2

- migrate Tower to `scope_type='global'`
- stop using the fake or sentinel project as Tower's runtime identity
- update restore logic to use explicit scope + provider checks

### Phase 3

- introduce `context_entries` or equivalent generic context ownership
- migrate global/project durable markdown and related storage over time

### Phase 4

- remove obsolete Tower/project coupling
- narrow or remove legacy provider-on-project assumptions
- consider deleting legacy `sessions.project_id` coupling for Tower paths once no longer needed

## Edge cases

### App reboot / restore

On restore, a session is reusable only if:

- the backing process is still healthy
- its provider matches the current global provider
- its scope matches the requested ownership

If not, it should be treated as stale and recreated on demand or immediately.

### Provider changed while app was down

Old live sessions from the previous provider should not silently become canonical after reboot. They must be treated as stale-for-provider.

### No project exists

Tower should still be able to start because it is global-scoped. This is one of the main reasons to remove its fake project anchoring.

### Active project work during replacement

Leader and Ace replacement must preserve goal/task/project context, but should not fake terminal continuity as if the original process survived.

## Consequences

This design makes provider switching easier to explain and reason about. Provider choice becomes a session lifecycle concern instead of a misleading project-property trap. Tower regains a data model that matches its product meaning. Restore behavior becomes more robust because scope and provider compatibility are checked explicitly instead of being inferred from a reused project id or sentinel row.

The main cost is temporary schema and compatibility complexity: `sessions.project_id` and `projects.agent_provider` may both need to coexist with newer fields during the migration. That is acceptable because it lowers rollout risk and keeps the refactor honest.

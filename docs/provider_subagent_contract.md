# Provider Helper Subagent Contract

## Purpose

Provider helper subagents are provider-native background workers that can assist
Tower, Leader, or Ace sessions without becoming part of the ATC command chain.
They are optional execution helpers only. Tower, Leader, and Ace remain the
operator-visible roles and the authoritative owners of ATC state.

## Hard boundaries

- ATC owns truth, persistence, visibility policy, token attribution, and allowed
  state mutation paths.
- Provider modules own provider-native helper execution mechanics.
- Shared ATC modules must not parse Codex/Claude helper syntax, prompts, event
  formats, or filesystem details.
- Helper output cannot directly mutate canonical ATC state. Any state change must
  go through existing ATC API/DB/event paths and be attributable to the parent
  Tower/Leader/Ace role.
- Audit logging is always on. Visibility controls only what is displayed.
- Token usage remains token-only. No cost/dollar semantics belong in helper
  records or helper token attribution.

## Global settings

```yaml
provider_helpers:
  enabled: true
  default_visibility: hidden  # hidden | summary | full
  audit_enabled: true         # locked on by backend behavior
```

`enabled` controls whether providers may use helpers. `default_visibility`
controls display behavior. `audit_enabled` is always treated as true so hidden
helper work is still reviewable after the fact.

Backend settings endpoints:

- `GET /api/settings/provider-helpers`
- `PUT /api/settings/provider-helpers`

## Visibility modes

| Mode | Meaning |
|---|---|
| `hidden` | Normal workflow UI does not show helper panels; audit rows/events are still recorded. |
| `summary` | UI may show compact lifecycle/status/result records. |
| `full` | UI may show prompt, output, events, actions, timings, token details, warnings, and errors. |

Visibility is display-only. Changing it must not start, stop, cancel, or alter
helper execution.

## Provider-neutral request shape

The shared contract is `atc.providers.helpers.ProviderHelperRequest`:

```python
ProviderHelperRequest(
    provider="codex",
    parent_session_id="session-id",
    parent_role="tower",      # tower | leader | ace
    purpose="project_status_check",
    prompt="Summarize current blockers...",
    project_id="project-id",
    task_id=None,
    helper_id=None,
    visibility="hidden",      # hidden | summary | full
    allowed_tools=("read",),
    allowed_actions=(),
    metadata={},
)
```

Provider modules may translate this into provider-native helper mechanics, but
providers should emit provider-neutral audit records and events back to ATC.

## Durable audit model

Phase 3 adds two durable tables:

### `provider_helper_runs`

One row per helper run, including:

- `provider`
- `helper_id`
- `parent_session_id`
- `parent_role`
- `project_id`
- `task_id`
- `purpose`
- `visibility`
- `status`
- timestamps
- prompt/output/summary/error fields
- provider-neutral metadata JSON

### `provider_helper_events`

Append-only event timeline per helper run:

- `helper_run_id`
- `event_type`
- `timestamp`
- `message`
- `payload_json`

Typical event names:

- `helper_requested`
- `helper_started`
- `prompt_submitted`
- `provider_output_received`
- `action_requested`
- `action_completed`
- `token_usage_recorded`
- `helper_completed`
- `helper_failed`

## Token attribution

Provider helpers should attribute token usage to the parent session/project while
preserving helper metadata such as `helper_run_id`, `helper_purpose`, provider,
and external provider session IDs in token metadata/raw usage details. The usage
path stays provider-neutral and token-only.

## Settings UI shell

The Settings pane includes a Provider Helper Subagents section for the global
helper enablement and default visibility controls. The UI deliberately treats
visibility as display policy only and labels audit logging as always on. Later
provider-specific helper phases should use these tables/contracts rather than
inventing provider-specific helper state.

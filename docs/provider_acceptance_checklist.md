# Provider Acceptance Checklist

Use this checklist in every PR that adds or materially changes a CLI-backed provider/platform.

## Provider boundary lock

- [ ] Provider-specific logic is contained under the provider module.
- [ ] Shared orchestration/session/token/terminal interfaces were not changed for a single provider.
- [ ] If a shared interface changed, a separate design log explains the generic need and compatibility tests were added.
- [ ] Shared modules receive only normalized ATC facts/events/statuses/token increments.
- [ ] Provider-specific filesystem paths, event names, prompt text, and JSON payload shapes do not leak into shared modules.
- [ ] Boundary regression scan is included and documented.

## CLI availability and config

- [ ] Provider CLI command discovery is implemented.
- [ ] Missing CLI error is operator-readable.
- [ ] PATH/command override behavior is documented if supported.
- [ ] Provider can be enabled/disabled by config.
- [ ] Provider-specific telemetry paths/globs/intervals are configurable when applicable.
- [ ] `config.local.yaml` can override local machine details.

## Session lifecycle

- [ ] ATC session row is created before terminal/provider process spawn.
- [ ] External provider session ID maps to ATC session ID.
- [ ] Start behavior is tested.
- [ ] Stop/shutdown behavior is tested.
- [ ] Reconnect/recovery behavior is tested or explicitly documented as unsupported.
- [ ] Unknown/unmapped provider sessions are skipped or surfaced safely; they do not create orphan task/usage rows.

## Tower / Leader / Ace orchestration

- [ ] Tower can start or recover the provider-backed Leader path.
- [ ] Leader can spawn provider-backed Ace sessions.
- [ ] Leader can assign tasks to Aces.
- [ ] Ace reports active/accepted state.
- [ ] Ace completion/report path reaches Leader/Tower/operator-visible surfaces.
- [ ] Acceptance evidence follows `Operator → Tower → Leader → Ace` rather than direct Ace-only execution.

## Terminal and prompt handling

- [ ] Provider launch uses existing terminal/session infrastructure or an approved documented exception.
- [ ] Terminal output streams to the UI.
- [ ] Resize behavior is preserved.
- [ ] Manual scrollback remains usable while output streams.
- [ ] Live-follow resumes only after scrolling back to bottom.
- [ ] Prompt submission sends instruction text and Enter atomically.
- [ ] Shared terminal code does not encode provider prompt/auth text.

## Trust, auth, and blocked states

- [ ] First-run trust/auth prompts are detected when applicable.
- [ ] Secret prompts are never auto-filled.
- [ ] Secret-like fixture/log/doc values are replaced with `[REDACTED]`.
- [ ] Blocked state is visible to the operator.
- [ ] Recovery guidance is available.
- [ ] Tests cover blocked/trust/auth paths or document why the provider has none.

## Token usage

- [ ] Token telemetry source is identified.
- [ ] Provider parser fixtures are added.
- [ ] Provider-specific token parsing lives inside the provider module.
- [ ] Provider emits normalized `TokenUsageIncrement` values.
- [ ] Token classes are preserved when available: input, cached input, output, reasoning output, total.
- [ ] Cumulative provider totals are converted to increments behind the provider boundary.
- [ ] Restart/re-read/backfill does not double-count.
- [ ] Unknown/unmapped telemetry is skipped or reported safely.
- [ ] Token summaries/API/UI reflect provider usage when applicable.

## Runtime truth and recovery

- [ ] Provider status is available through API or runtime status surfaces.
- [ ] Health/status includes unavailable/blocked/ready/running/failure states as applicable.
- [ ] Structured failure logs/reason codes are used.
- [ ] Recovery actions are deterministic and tested where possible.
- [ ] DB/API/terminal/frontend/provider state can be reconciled.

## Frontend / operator visibility

- [ ] Provider-specific operator actions are visible when needed.
- [ ] Provider blocked/misconfigured state is visible.
- [ ] Token sync/status is visible when provider has token telemetry.
- [ ] Manual sync/backfill controls are visible when supported.
- [ ] Frontend unit tests cover changed UI.
- [ ] Playwright evidence is attached when workflow/UI behavior changes.

## Cost/dollar prohibition

- [ ] No legacy dollar-denominated accounting fields.
- [ ] No pricing registry.
- [ ] No dollar budgets or billing limits.
- [ ] No cost-specific usage/Tower endpoints.
- [ ] Token limits are enforced using token counts only.
- [ ] Stale cost-term scan is included.

## Documentation

- [ ] Provider-specific design log was created from `docs/templates/provider_onboarding_template.md`.
- [ ] `docs/provider_implementation_map.md` was updated if new files/patterns were introduced.
- [ ] `docs/FEATURES.md` was updated for operator-visible behavior changes.
- [ ] `docs/ARCHITECTURE.md` was updated for subsystem responsibility changes.
- [ ] README was updated if a new top-level entrypoint was added.

## Validation evidence

- [ ] Backend unit tests.
- [ ] Integration/runtime tests where lifecycle or orchestration changed.
- [ ] Frontend tests where UI changed.
- [ ] Playwright smoke where operator workflow changed.
- [ ] Provider-boundary scan.
- [ ] Stale cost-term scan.
- [ ] Post-merge validation on `main`.

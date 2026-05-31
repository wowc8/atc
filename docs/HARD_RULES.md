# ATC Hard Rules

These are non-negotiable rules for future development in this repository.

## 1. Documentation must stay in sync with code
If you make a code change, you must review the relevant `.md` docs and update them if they are stale.

This is a hard requirement.

Minimum docs to consider on any change:
- `docs/START_HERE.md`
- `docs/CODEBASE_MAP.md`
- `docs/HARD_RULES.md`
- `docs/DEVELOPMENT_RULES.md`
- `docs/TESTING_RULES.md`
- relevant feature/refactor docs
- relevant architecture/API docs

Do not consider work complete if the code changed but the docs were left stale.

## 2. Shared orchestration code must stay provider-neutral
Provider-specific behavior must not leak into shared orchestration code.

## 3. One runtime boundary only
Tower, Leader, and Ace runtime flows must go through the central runtime service once that path exists.

## 4. tmux is shared infrastructure
Generic tmux operations belong in shared runtime infrastructure, not duplicated provider code.

## 5. Provider behavior lives in provider-owned modules
Provider command construction, readiness logic, bootstrap behavior, delivery quirks, and recovery rules belong in provider-owned modules or wrapper implementations.

## 6. No new provider-specific hacks in shared code
Do not add shared-code conditionals for provider-specific sleeps, flags, prompt quirks, or delivery behavior.

## 7. Role vocabulary must stay canonical
Use:
- `tower`
- `leader`
- `ace`

Do not deepen old terminology drift in new code.

## 8. Machine-readable wrapper/runtime markers are contract surface
If you change them, update docs and callers intentionally.

## 9. No silent fallback to stale architecture
Do not hide bypass paths or mixed old/new runtime behavior behind silent fallback.

## 10. Boundary-changing work must update docs in the same workstream
Architecture changes, API changes, runtime-provider changes, and testing expectations must update the corresponding docs before the work is done.

## 11. Session provider identity is immutable for the life of a session row
A session row's provider is part of its runtime identity.

Do not mutate an old session row to pretend it was created under a different provider.
If the desired provider changed, create a replacement session instead of reusing/mutating the old one.

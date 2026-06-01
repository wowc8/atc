# ATC Development Rules

These rules are for keeping the codebase clean as it evolves.

## 1. Prefer clear ownership over convenience
If you are adding logic and it is not obvious where it belongs, stop and decide the ownership boundary first.

## 2. Do not deepen drift
Known drift areas already exist:
- `leader` vs `manager`
- `tasks` vs `task_graphs`
- partial provider abstraction vs direct tmux/session control

New code should reduce these problems, not extend them.

## 3. Push logic downward when appropriate
- workflow/policy belongs in orchestration/application code
- provider-specific terminal behavior belongs in provider runtime/wrapper code
- generic tmux behavior belongs in shared runtime/tmux infrastructure

## 4. Avoid monolithic growth
Be cautious about adding more responsibilities to already-large files such as:
- `src/atc/state/db.py`
- `src/atc/tower/controller.py`
- `src/atc/api/routers/projects.py`
- `frontend/src/context/AppContext.tsx`

If possible, extract smaller focused modules instead of extending broad ones.

## 5. Refactor toward deletion, not permanent layering
Temporary compatibility shims are fine.
Permanent duplicate abstractions are not the goal.

## 6. Update docs as part of development
Do not treat docs as end-of-project cleanup. Update them in the same workstream.

## 7. Make new boundaries explicit
When introducing a new architectural boundary, create or update the durable doc that explains it.

## 8. Make changes readable for future agents
Leave behind structure and docs that reduce the need for future deep rediscovery.


- `make cleardb` is a local reset command only. It should stop dev processes and clear DB/state/cache, but it must not pull code, rebuild the venv, or auto-start the app. Use `make setup` and `make dev` explicitly after reset.

- Avoid import-time cycles between legacy providers and session lifecycle modules. If a provider only needs a session helper for spawn-time behavior, prefer a narrow local import at call time instead of a module-level cross-import.
- The same import-cycle guard applies to OpenCode and other legacy providers too, not just Claude. Provider modules must not import `session.ace` helpers at module import time when a local call-site import will do.
- Codex legacy provider imports follow the same rule: no module-level import from `session.ace` when a local spawn-path import avoids the cycle.

# Executive summary
ATC’s frontend is a React + Vite SPA wrapped by a very thin Tauri shell. The main architectural center is `frontend/src/context/AppContext.tsx`, which bootstraps state from REST, keeps it live via a shared WebSocket, and feeds route-level screens for dashboard, project workspace, usage, and context. The desktop side in `src-tauri/src/lib.rs` mostly just starts plugins and spawns the Python `atc-server` sidecar, so almost all product behavior lives in the web app. The strongest refactor seams are around state management and data fetching, repeated imperative fetch-on-render patterns, duplicated session-control logic across Tower/Leader/Ace components, and a settings/test surface that has drifted from the routed app.

# ATC frontend and desktop codebase map

## Scope mapped
- `atc/frontend/src`
- `atc/frontend/tests`
- `atc/src-tauri`

## High-level UI architecture
- Entry point: `frontend/src/main.tsx`
  - Initializes Sentry, wraps app in `ErrorBoundary`, and mounts a `QueryClientProvider`.
  - React Query is configured, but the actual app mostly does not use it yet. Most fetching is manual via `api` and local state.
- Router and shell: `frontend/src/App.tsx`
  - Global shell is `TowerBar` on top. Dashboard/project routes use a two-column shell split: route content on the left, persistent `TowerPanel` on the right, with a single shell-owned resize handle between them. Non-Tower routes keep the bottom `TowerPanel` fallback.
  - Routes:
    - `/dashboard` → `pages/Dashboard.tsx`
    - `/projects/:id` → `pages/ProjectView.tsx`
    - `/usage` → `pages/UsagePage.tsx`
    - `/context` → `pages/ContextPage.tsx`
  - `StartupGate` blocks the UI in Tauri until the sidecar backend responds.
- App-wide state and live updates: `frontend/src/context/AppContext.tsx`
  - This is the real app hub.
  - Holds normalized-ish global state for projects, leaders, sessions, task graphs, budgets, usage, GitHub summaries, notifications, tower detail/progress, failure logs, and heartbeats.
  - Hydrates via `fetchAll()` on backend readiness.
  - Applies incremental updates from WebSocket channels.

## State and data flow
### Core pattern
1. `useBackendReady()` waits for `/api/health` in Tauri.
2. `AppProvider.fetchAll()` performs a fan-out REST bootstrap.
3. Components read from context state and call `api` directly for mutations.
4. WebSocket messages patch the reducer state in near real time.
5. Some components still call `fetchAll()` after mutations instead of doing targeted cache updates.

### Central files
- `frontend/src/context/AppContext.tsx`
- `frontend/src/utils/api.ts`
- `frontend/src/hooks/useWebSocket.ts`
- `frontend/src/hooks/useTerminal.ts`
- `frontend/src/types/index.ts`

### REST bootstrap in `fetchAll()`
`AppContext` loads:
- `/projects`
- per project `/projects/:id/aces`
- per project `/projects/:id/manager`
- per project `/projects/:id/task-graphs`
- `/heartbeat`
- `/failure-logs?limit=200`
- `/tower/status`

Notable gap: `initialState` includes `tasks`, `budgets`, `notifications`, and `github`, but `fetchAll()` does not populate all of them here. Some of those surfaces appear partial, legacy, or dependent on websocket payloads / page-local fetches.

### Reducer shape
`AppContext` reducer is a straightforward switch reducer with:
- bulk setters (`SET_PROJECTS`, `SET_LEADERS`, etc.)
- event-like updaters (`ADD_PROJECT`, `UPDATE_SESSION_STATUS`, `UPDATE_HEARTBEAT`)
- minimal denormalization, mostly storing arrays plus a few maps keyed by project/session id

### API client
`frontend/src/utils/api.ts`
- Uses `fetch` with JSON defaults and a 30s timeout.
- Switches base URL between `/api` in browser dev and `http://127.0.0.1:8420/api` in Tauri.
- Wraps structured backend errors in `ApiError` with `code` and `extra`.

## WebSocket and realtime integration
### Shared app stream
`frontend/src/hooks/useWebSocket.ts`
- Connects to `/ws`.
- Subscribes by sending `{ channel: "subscribe", data: [...] }`.
- Handles reconnect with exponential backoff + jitter.
- Used centrally by `AppContext` for channels:
  - `state`
  - `failure_logs`
  - `tower`
  - `heartbeat`

### Message handling in `AppContext`
- `state` channel:
  - full/partial state patches
  - project create/update/delete
  - session create/status change
  - task graph refresh for one project
- `tower` channel:
  - tower state changes
  - current tower session id
  - leader session id
  - progress counts
  - leader activity preview
- `heartbeat` channel:
  - session liveness state (`alive`, `stale`, `stopped`)
- `failure_logs` channel:
  - append and resolve log entries

### PTY / terminal streams
`frontend/src/hooks/useTerminal.ts`
- Separate per-terminal WebSocket connection pattern.
- Builds xterm.js terminal instances and subscribes to `terminal:<session_id>` channels.
- Sends user input and resize events back over WS.
- Buffers writes until terminal is attached, and re-requests snapshots for hidden/collapsed panes.
- Contains output cleanup for overly long box-drawing separators.

This hook is the core integration seam for:
- Tower terminal
- Leader terminal
- Ace terminal

## Main screens and components
## 1. Dashboard
Files:
- `pages/Dashboard.tsx`
- `components/dashboard/ProjectGridView.tsx`
- `components/dashboard/ProjectRowView.tsx`
- `components/dashboard/ProjectBoardView.tsx`

Responsibilities:
- top summary cards for tokens, sessions, notifications
- create project entry point
- three project list modes: grid, row, board
- localStorage persistence for preferred dashboard view
- archive/delete project actions
- optimistic local reorder in all three project presentations

Patterns:
- Grid and row views reorder via `PATCH /projects/reorder`.
- Board view additionally changes project status by drag-and-drop across columns.
- Uses `dnd-kit` heavily for ordering and cross-column moves.
- Dashboard keeps a local `orderedProjects` array layered over context state for optimistic UX.

Important components:
- `ProjectGridView`: card-heavy summary, archive/delete actions
- `ProjectRowView`: denser operational list
- `ProjectBoardView`: status columns for active/paused/archived

## 2. Project workspace
Main file: `pages/ProjectView.tsx`

This is the densest operational screen. Layout is custom split-pane UI with persisted sizes.

Structure:
- Left column
  - `leader/TaskBoard.tsx` at top
  - `ace/AceList.tsx` or `ace/AceConsole.tsx` below
- Right column
  - `leader/LeaderConsole.tsx`
- Bottom full-width
  - `context/ContextHub.tsx`

Key behaviors:
- panel sizes persisted in localStorage
- expanded ace console swaps in place with compact ace card list
- route-driven project lookup from app context

### Leader surface
`components/leader/LeaderConsole.tsx`
- Starts/stops project leader via REST.
- Auto-starts if provider is `claude_code`.
- Attaches terminal through `useTerminal`.
- Has a tabbed lower section:
  - `GitHubPanel`
  - `BudgetPanel`

### Ace surface
- `components/ace/AceList.tsx`
- `components/ace/AceConsole.tsx`
- `components/ace/AceTerminal.tsx`

Capabilities:
- create, start, stop, delete ace sessions
- show session health and status badges
- map assigned task graph title onto each ace
- compact list mode embeds mini terminals per ace card
- expanded console mode gives tabbed ace switching

### Task surface
`components/leader/TaskBoard.tsx`
- Local create form for task graphs
- Toggle between kanban and table modes
- Currently mostly presentational after creation, with no inline edit/drag/status mutation flow

### Context surface
`components/context/ContextHub.tsx`
- Reused in both project page and dedicated context page
- Supports scopes: `global`, `project`, `tower`, `leader`, `ace`
- CRUD for context entries
- Polls every 5 seconds instead of using websocket updates
- Inline edit/save, restricted toggle, delete confirmation

## 3. Usage page
Main file: `pages/UsagePage.tsx`
- Uses Recharts for token and CPU/RAM charts.
- Pulls summary numbers from app context, but chart series are fetched locally.
- Period selector drives token refetch.
- Budget utilization list reads `budgets` from app context.

Important note:
- Fetching is triggered imperatively during render using `if (!loaded) void fetch...`, not from `useEffect`. It works, but it is a fragile anti-pattern.

## 4. Context page
Main file: `pages/ContextPage.tsx`
- Thin page wrapper around `ContextHub` in global mode with scope tabs enabled.

## Global shell surfaces
### Tower bar
`components/tower/TowerBar.tsx`
- Primary app chrome and nav
- status dot from tower state
- token/project metrics
- notification badge
- failure log badge
- opens `LogViewer` and `SettingsPane`

### Tower panel
`components/tower/TowerPanel.tsx`
- Persistent bottom terminal panel, conceptually like an IDE integrated terminal
- Auto-starts Tower when idle unless user explicitly stopped it
- Resolves context project from route when possible
- Shows ticker, progress counts, start/stop controls
- Uses `useTerminal` for live PTY

### Failure log viewer
`components/tower/LogViewer.tsx`
- Filterable failure log list
- Can copy a Claude-friendly incident bundle to clipboard
- Can send Sentry reports
- Can mark logs resolved via API

## Settings and provider UX
### Current settings surface
There is no routed `/settings` page in `App.tsx`. Settings are exposed as an overlay pane opened from `TowerBar`:
- `components/tower/SettingsPane.tsx`

### What the pane actually contains
- Read-only backend URL and simple “Connected” indicator
- GitHub default org stored in localStorage
- `BackupPanel`
- `ResourceLimitsPanel`
- Tower status summary

### Backup UX
`components/settings/BackupPanel.tsx`
- Richest settings subpanel
- shows backup status and recent backup logs
- create backup now
- restore from path
- connect Dropbox / Google Drive through auth URL + pasted code flows

### Resource limits UX
`components/settings/ResourceLimitsPanel.tsx`
- loads and saves `/settings/resource-limits`
- edits max ace concurrency and CPU/RAM throttle/pause thresholds

### Feature flags / provider-related drift
- `components/settings/FeatureFlagsPanel.tsx` and `hooks/useFeatureFlags.ts` exist.
- `FeatureFlagsPanel` does not appear to be wired into the main shell or routed pages.
- Provider UX at project creation is minimal: `CreateProjectModal` captures name/description/repo path/GitHub repo, but not provider selection despite `Project.agent_provider` existing in types and leader behavior depending on provider.

Implication: provider configuration appears to be backend-driven or incomplete on the frontend.

## Tauri desktop shell integration
Central files:
- `src-tauri/src/lib.rs`
- `src-tauri/src/main.rs`
- `src-tauri/tauri.conf.json`

### What Tauri does today
- Registers process and shell plugins in setup.
- Spawns sidecar binary `atc-server` at startup.
- Loads built frontend from `../frontend/dist`.
- Opens one main window at 1280x800.

### Frontend Tauri-awareness
- Tauri detection pattern is consistent: check `"__TAURI_INTERNALS__" in window`.
- Used for:
  - startup gating (`useBackendReady`)
  - choosing REST base URL
  - choosing WS host
  - updater flow (`useUpdater`)
- Updater UI exists in frontend (`UpdateBanner`, `useUpdater`), but `tauri.conf.json` has `createUpdaterArtifacts: false`, so desktop update support looks only partially enabled.

## Recurring frontend patterns
### Common good patterns
- Clear file/domain grouping by feature (`tower`, `leader`, `ace`, `dashboard`, `context`, `settings`)
- Centralized TS model definitions in `types/index.ts`
- Confirm actions wrapped in reusable `ConfirmPopover`
- Strong use of `data-testid` on important surfaces
- Reusable live terminal abstraction in `useTerminal`

### Common implementation style
- REST mutations are mostly issued directly from components.
- Many mutations call `onRefresh` or `fetchAll` instead of updating local state surgically.
- Local UI state is frequently persisted in localStorage.
- Several components lazy-fetch their own secondary data rather than using global context.

## Tests and frontend verification surface
### Unit/integration tests under `frontend/src`
Examples:
- `context/__tests__/AppContext.test.tsx`
- `pages/__tests__/Dashboard.test.tsx`
- `pages/__tests__/ProjectView.test.tsx`
- `pages/__tests__/UsagePage.test.tsx`
- `hooks/useWebSocket.test.ts`
- `utils/__tests__/api.test.ts`

These mostly exercise rendering and low-level behavior, not deep live workflows.

### E2E tests under `frontend/tests/e2e`
- `dashboard-views.spec.ts`
- `atc-qa.spec.ts`
- `qa-full.spec.ts`

Notable drift:
- `dashboard-views.spec.ts` tests `/settings`, but the app router currently has no `/settings` route. Settings now live in a shell pane. That is a concrete sign of test/product divergence.

## Tech debt and refactor seams
### 1. App state management is doing too much in one provider
`AppContext.tsx` is the central nervous system, but it now owns:
- initial load orchestration
- reducer logic
- websocket event translation
- backend readiness coupling

Good refactor seam:
- split into data domains or custom stores/hooks, for example projects/sessions, tower, logs, usage
- or actually adopt React Query for server state and keep context for only UI/session state

### 2. React Query is present but largely unused
`main.tsx` installs `QueryClientProvider`, but most of the app still uses hand-written fetch state.
This creates duplicated loading/error logic and manual invalidation.

### 3. Imperative fetches inside render
Several components use patterns like `if (!loaded) void fetchPrs()` or `if (!usageLoaded) void fetchTokenUsage(period)` inside component bodies.
This is risky under StrictMode and makes render side-effectful.
Targets include:
- `UsagePage.tsx`
- `GitHubPanel.tsx`

### 4. Repeated start/stop session logic
Tower, Leader, and Ace surfaces each implement similar REST control and optimistic UI logic separately.
A shared session-control hook/service would reduce duplication and inconsistent behavior.

### 5. Mixed data freshness model
The app uses:
- websocket push for state/tower/logs/heartbeats
- polling for context entries
- full `fetchAll()` refreshes after many mutations
- page-local fetches for usage charts and GitHub PRs

This works, but creates uneven responsiveness and more state synchronization burden.

### 6. Settings surface is fragmented and partially orphaned
- `SettingsPane` is the actual entry point.
- `FeatureFlagsPanel` exists but appears unmounted.
- E2E still assumes a `/settings` page.
- provider configuration is not visible in project creation UX.

### 7. Model/type mismatch or partially implemented state
`AppState` includes structures that are not fully bootstrapped in `fetchAll()`.
That suggests either backend websocket dependence, unfinished migration, or stale state fields.
Worth auditing:
- `tasks`
- `budgets`
- `notifications`
- `github`

### 8. Terminal integration complexity is concentrated in one hook
`useTerminal.ts` is effective but fairly intricate, with buffering, reconnects, resize syncing, and hidden-container recovery. It is a valuable abstraction, but also a hotspot where regressions would affect Tower, Leader, and Ace simultaneously.

## Most central files to read first
If someone is onboarding, the best reading order is:
1. `frontend/src/App.tsx`
2. `frontend/src/context/AppContext.tsx`
3. `frontend/src/utils/api.ts`
4. `frontend/src/hooks/useWebSocket.ts`
5. `frontend/src/hooks/useTerminal.ts`
6. `frontend/src/pages/Dashboard.tsx`
7. `frontend/src/pages/ProjectView.tsx`
8. `frontend/src/components/tower/TowerPanel.tsx`
9. `frontend/src/components/leader/LeaderConsole.tsx`
10. `src-tauri/src/lib.rs`

## Net assessment
The frontend already has a coherent operator-console shape: dashboard for project fleet management, deep per-project workspace, persistent tower terminal, and supporting usage/context/settings overlays. Its main challenge is not surface area but consistency: state fetching, live updates, and settings/provider affordances are implemented in several different styles. The cleanest next refactor would be to consolidate server-state access patterns and make the routed/product-tested surface match the actual shell UX.
# 015 — Desktop API origin policy

## Context

Release `v0.1.0-rc21` (`0b37ce0`) loads the packaged Tauri frontend from
`index.html` and fetches `http://127.0.0.1:8420/api/health` using browser `fetch`.
There was no CORS middleware. An HTTP 200 therefore did not imply that the
webview could read the response: macOS's `tauri://localhost` origin requires an
explicit `Access-Control-Allow-Origin` response header. The readiness hook
catches fetch errors and retries, leaving the startup gate visible. WebSocket
acceptance does not establish REST CORS access.

## Decision

Use FastAPI's standard CORS middleware in the app factory:

- Exact origins: `tauri://localhost` and `http://tauri.localhost` (the default
  Windows custom-protocol origin).
- Explicit methods: GET, POST, PUT, PATCH, DELETE.
- Explicit non-safelisted request header: `Content-Type`, used by the JSON client.
- No credentials, origin regex, wildcard origins, or `null` origin permission.
- Do not add browser development origins: Vite already proxies `/api` and `/ws`.

This applies to REST responses and JSON preflights, not only the health route,
so the rest of the UI works after readiness succeeds. CORS is a browser access
policy, not authentication or a replacement for loopback binding. Rejected
origins do not receive permission to read responses; simple requests may still
reach routes. WebSocket origin policy is separate and unchanged.

## Scope boundaries

`duration_ms` in `/api/health` is the cached startup smoke-test measurement,
not request latency. The smoke test runs during lifespan startup; GET health
reads its stored result. A value such as 4335 ms does not demonstrate that the
hook's 4000 ms per-request timeout is too short. Leave that timeout unchanged
without actual request-timing evidence. Both `ok` and `degraded` health statuses
already satisfy the hook.

The Rust shell drops its sidecar event receiver and child handle at setup exit.
That discards application-level process diagnostics and deserves independent
lifecycle review, but it is not established as the cause of a startup gate that
persists against a manually running, responding backend. No Rust lifecycle
changes are bundled here. The repository specifies shell plugin major version 2
without a tracked Cargo lockfile, so current upstream process-reader behavior
is not proof of the exact rc21 packaged dependency behavior.

## Verification

Regression tests exercise both allowed origins, healthy/degraded responses with
a cached 4335 ms smoke result, JSON preflights for each client method, rejected
web/opaque/lookalike origins, rejected extra permissions, HTTP errors, and
unchanged requests without Origin. Before middleware, the regression suite
reproduced HTTP 200 without the CORS header and HTTP 405 on JSON preflight.
Native rc21/M5 webview confirmation remains a user-side diagnostic.

Against the user's running backend:

```bash
curl -sS -i --max-time 10 \
  -H 'Origin: tauri://localhost' \
  -w '\nrequest_total_seconds=%{time_total}\n' \
  http://127.0.0.1:8420/api/health
```

The fixed backend returns `Access-Control-Allow-Origin: tauri://localhost` and
`Vary: Origin`. An HTTP 200 without that allow-origin header confirms the missing
permission for this origin; curl itself does not enforce browser CORS. The
reported request time is separate from the JSON smoke duration.

```bash
curl -sS -i --max-time 10 -X OPTIONS \
  -H 'Origin: tauri://localhost' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: content-type' \
  http://127.0.0.1:8420/api/projects
```

The fixed backend returns 200 with explicit origin, method, and header permissions.

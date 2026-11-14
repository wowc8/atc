# Desktop backend working directory and diagnostics

## Decision

The packaged Tauri launcher explicitly uses the OS-resolved HOME directory for
its bundled Python backend. Finder-like launches can inherit `/`; Python's
relative `atc.db` and config lookup then fail before the listener starts. This was
reproduced with the unchanged rc22 CI sidecar, isolated HOME, and minimal PATH:
`/` exits 3 with SQLite `unable to open database file`; HOME reaches health.

HOME is the minimal compatibility policy: reuse existing HOME database/config,
including relative paths, without silently creating a new empty app-data store.
The launcher rejects missing/relative HOME rather than falling back to cwd.
CLI/development settings resolution is unchanged. Future relocation requires an
explicit backed-up migration policy; it is not part of this fix.

The shell retains its child handle and drains both process output streams into
`~/.atc/logs/desktop-backend.log` (fresh each launch). A persisted Rust startup
error command lets the readiness gate detect setup/exit failures even if they
occur before React mounts; health still establishes success. Normal application
exit kills only the owned child. No global port cleanup or authentication changes
are introduced. Abrupt OS termination is not a graceful application-exit test.

## Verification

- Rust tests reject invalid HOME and preserve the selected absolute HOME.
- Hook regressions cover persisted failure, health success, and disabled mode.
- `scripts/verify_desktop_cwd.py` tests actual packaged sidecar behavior from `/`
  versus HOME with existing disposable database/config and minimal PATH.
- Local rebuilt native shell cold-launched from `/`, isolated HOME, minimal
  `/usr/bin:/bin:/usr/sbin:/sbin` PATH, short disposable TMUX_TMPDIR, and no existing
  listener reached `status: ok`. Normal native Quit exited 0 and cleared port 8420.
- Native Dashboard screenshot acceptance is **blocked**: the current macOS console
  reports `CGSSessionScreenIsLocked: true`; captured window OCR contains no route
  text. Do not substitute this capture or earlier rc22 screenshots for Dashboard
  proof. Rerun with the console unlocked before merging/releasing.
- Evidence is under the agent workspace `test-results/atc-desktop-cwd-resume-20261113/`
  (`native-report.json`, `console-state.json`, `native-ocr.txt`) and
  `test-results/atc-desktop-cwd-resume-repro-20261113/` (`report.json` and sidecar logs).
  This uses a locally modified bundle, not a new published installer. Native UI
  and published-installer verification remain separate release gates.

"""Ace session lifecycle — create / start / stop / destroy.

Follows the DB-first pattern:
  1. Write session row to DB (status: connecting)
  2. Publish ``session_created`` event
  3. Spawn tmux pane
  4a. Success → update row (status: idle, tmux_pane: <id>)
  4b. Failure → update row (status: error)

Creation reliability features (design doc §10a):
  - DB-first: row always written before tmux pane spawned
  - Atomic Enter: instruction text + Enter sent with no await gap
  - TUI readiness: alternate_on checked before any keystroke
  - Verification loop: t+10s / t+60s / t+120s post-creation checks
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path as _Path
from typing import TYPE_CHECKING, Any

from atc.agents.base import ProviderSpawnRequest
from atc.agents.claude_runtime import accept_startup_dialogs
from atc.session.state_machine import (
    SessionStatus,
    transition,
)
from atc.state import db as db_ops

if TYPE_CHECKING:
    import aiosqlite  # type: ignore[import-not-found]

    from atc.core.events import EventBus

logger = logging.getLogger(__name__)

# Default tmux session name used for ATC panes
ATC_TMUX_SESSION = "atc"


# ---------------------------------------------------------------------------
# tmux helpers (thin wrappers around subprocess)
# ---------------------------------------------------------------------------


def _compatible_nvm_bins(home: str) -> list[str]:
    """Return nvm bin dirs where node actually runs on this OS.

    Newer Node versions (v20+) require macOS 13+ and crash with dyld
    symbol errors on older systems like Big Sur (11). We probe each
    candidate with ``node --version`` and return only working ones.
    """
    import glob as _g
    import os as _os2
    import subprocess as _sp

    result = []
    for p in sorted(_g.glob(f"{home}/.nvm/versions/node/*/bin"), reverse=True):
        node_bin = _os2.path.join(p, "node")
        if not _os2.path.isfile(node_bin):
            continue
        try:
            rc = _sp.run(
                [node_bin, "--version"],
                capture_output=True,
                timeout=3,
            ).returncode
            if rc == 0:
                result.append(p)
        except Exception:
            pass
    return result


def _build_env_prefix() -> str:
    """Build a shell env prefix that enriches PATH for tmux panes.

    SSH sessions on macOS (and some Linux deployments) inherit a minimal PATH
    that omits Homebrew (/usr/local/bin, /opt/homebrew/bin) and nvm/asdf/volta
    Node shims — so ``claude`` and ``tmux`` are not on PATH inside spawned panes.

    This prefix is prepended to every tmux pane command so that ``atc-agent``
    (and ``claude`` inside it) can be found regardless of how the backend was
    launched.
    """
    import os as _os
    extra_paths: list[str] = []
    home = _os.path.expanduser("~")
    candidates = [
        # Homebrew — Intel Mac
        "/usr/local/bin",
        # Homebrew — Apple Silicon
        "/opt/homebrew/bin",
        # nvm current symlink
        f"{home}/.nvm/current/bin",
        # nvm active version (resolve symlink)
        *([str(_Path(f"{home}/.nvm/current/bin").resolve())]
          if _Path(f"{home}/.nvm/current/bin").is_symlink()
          else []),
        # nvm versioned installs.
        # IMPORTANT: newer Node versions may require a newer macOS (e.g. Node 24
        # needs Monterey 13.5+, Big Sur only has 11). We check each bin dir's
        # `node` binary with a cheap `--version` call and skip any that fail
        # (dyld crash, missing OS symbols, etc.).
        *_compatible_nvm_bins(home),
        # volta
        f"{home}/.volta/bin",
        # asdf shims
        f"{home}/.asdf/shims",
        # fnm current
        f"{home}/.fnm/current/bin",
    ]
    current_path = _os.environ.get("PATH", "")
    for c in candidates:
        if c and _os.path.isdir(c) and c not in current_path:
            extra_paths.append(c)
    if not extra_paths:
        return ""
    enriched = ":".join(extra_paths) + ":" + current_path
    return f"PATH={shlex.quote(enriched)} "


def _tmux_binary() -> str:
    """Resolve the tmux binary path, checking common macOS/Linux locations."""
    tmux = shutil.which("tmux")
    if tmux:
        return tmux
    for candidate in ("/usr/local/bin/tmux", "/opt/homebrew/bin/tmux", "/usr/bin/tmux"):
        if shutil.which(candidate):
            return candidate
    return "tmux"  # fallback — will fail with a clear error


async def _tmux_run(*args: str) -> str:
    """Run a tmux command and return stdout."""
    proc = await asyncio.create_subprocess_exec(
        _tmux_binary(),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"tmux {' '.join(args)} failed: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def _ensure_tmux_session(session_name: str) -> None:
    """Create the tmux session if it doesn't already exist.

    Uses explicit dimensions so panes work correctly even when no real
    terminal is attached (PTY output streams to xterm.js in the frontend).
    The frontend sends a resize event once xterm.js measures its actual
    column width, so the initial size here is a reasonable default.
    """
    try:
        await _tmux_run("has-session", "-t", session_name)
    except RuntimeError:
        await _tmux_run("new-session", "-d", "-s", session_name, "-x", "120", "-y", "40")


async def _spawn_pane(
    session_name: str,
    command: str | None = None,
    *,
    working_dir: str | None = None,
) -> str:
    """Create a new window in *session_name* and return its pane id (e.g. ``%5``).

    Uses ``new-window`` instead of ``split-window`` to avoid the
    'no space for new pane' error that occurs when the terminal is small
    or headless (PTY output streams to the frontend xterm.js panel).
    """
    args = ["new-window", "-a", "-t", session_name, "-d", "-P", "-F", "#{pane_id}"]
    if working_dir:
        # Ensure the directory exists — tmux silently falls back to $HOME if the
        # working_dir does not exist, which causes Claude Code to start in the wrong place.
        import os as _os
        if not _os.path.isdir(working_dir):
            _os.makedirs(working_dir, exist_ok=True)
            logger.info("_spawn_pane: created missing working_dir %s", working_dir)
        args.extend(["-c", working_dir])
    if command:
        import shlex as _shlex

        from atc.agents.auth import is_oauth_key, resolve_agent_api_key

        key = resolve_agent_api_key()
        if key:
            if is_oauth_key(key):
                command = f"CLAUDE_CODE_OAUTH_TOKEN={_shlex.quote(key)} {command}"
            else:
                command = f"ANTHROPIC_API_KEY={_shlex.quote(key)} {command}"
        # Prepend PATH enrichment so tmux panes can find claude + other tools
        # regardless of how the backend was launched (e.g. via SSH with minimal PATH).
        command = _build_env_prefix() + command
        args.extend([command])
    logger.warning(
        "=== SPAWN_PANE DEBUG ===\n  tmux args: %s\n  working_dir: %s\n  command: %s",
        args,
        working_dir,
        command,
    )
    pane_id = await _tmux_run(*args)
    return pane_id


async def _accept_trust_dialog(pane_id: str, *, timeout: float = 10.0) -> bool:
    """Backward-compatible wrapper around Claude-specific startup handling."""
    return await accept_startup_dialogs(
        pane_id,
        capture_pane=_capture_pane,
        get_alternate_on=_get_alternate_on,
        tmux_run=_tmux_run,
        timeout=timeout,
    )


async def _kill_pane(pane_id: str) -> None:
    """Kill a tmux pane by id."""
    try:
        await _tmux_run("kill-pane", "-t", pane_id)
    except RuntimeError:
        logger.warning("Failed to kill pane %s (may already be dead)", pane_id)


async def _pane_is_alive(pane_id: str) -> bool:
    """Check if a tmux pane still exists."""
    try:
        await _tmux_run("has-session", "-t", pane_id)
        return True
    except RuntimeError:
        return False


async def _capture_pane(pane_id: str, *, lines: int = 50) -> str:
    """Capture the visible content of a tmux pane.

    Returns the last *lines* lines of pane output as a string.
    """
    return await _tmux_run("capture-pane", "-t", pane_id, "-p", "-S", f"-{lines}")


async def _get_alternate_on(pane_id: str) -> bool:
    """Check if the pane is in alternate screen mode (TUI active).

    Returns ``True`` when a full-screen TUI (like Claude) is active.
    """
    result = await _tmux_run("display-message", "-t", pane_id, "-p", "#{alternate_on}")
    return result.strip() == "1"


# Legacy wrapper kept for backward compatibility with existing callers
async def _send_keys(pane_id: str, keys: str) -> None:
    """Send keystrokes to a tmux pane (atomic: text + Enter in one call)."""
    await _tmux_run("send-keys", "-t", pane_id, keys, "Enter")


# ---------------------------------------------------------------------------
# Ace lifecycle
# ---------------------------------------------------------------------------


async def _spawn_provider_session(
    conn: aiosqlite.Connection,
    session_id: str,
    *,
    project_id: str | None,
    session_type: str,
    working_dir: str | None,
    context_file: _Path | None = None,
    launch_command: str | None = None,
) -> tuple[str, str]:
    from atc.agents.factory import create_provider

    project = await db_ops.get_project(conn, project_id) if project_id else None
    session = await db_ops.get_session(conn, session_id)
    provider_name = session.provider if session and session.provider else (
        project.agent_provider if project and project.agent_provider else "claude_code"
    )
    provider = create_provider(provider_name)

    session = await db_ops.get_session(conn, session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    role = {
        "ace": "ace",
        "manager": "leader",
        "tower": "tower",
    }.get(session_type, "ace")

    info = await provider.spawn_for_session(
        ProviderSpawnRequest(
            session=session,
            project=project,
            working_dir=working_dir,
            launch_command=launch_command,
            context_file=context_file,
            role=role,
        )
    )
    pane_id = str(info.metadata.get("pane_id") or "")
    tmux_session = str(info.metadata.get("tmux_session") or ATC_TMUX_SESSION)
    if not pane_id:
        raise RuntimeError(f"Provider {provider_name} did not return a pane_id for {session_id}")
    return tmux_session, pane_id


async def create_ace(
    conn: aiosqlite.Connection,
    project_id: str,
    name: str,
    *,
    task_id: str | None = None,
    host: str | None = None,
    event_bus: EventBus | None = None,
    working_dir: str | None = None,
    launch_command: str | None = None,
    deploy_spec_kwargs: dict[str, Any] | None = None,
) -> str:
    """Create an ace session (DB-first). Returns the session id.

    The session is created with status ``connecting`` and a tmux pane is
    spawned.  On success the status moves to ``idle``; on failure to ``error``.

    Args:
        working_dir: Working directory for the tmux pane (e.g. the repo path).
        launch_command: Shell command to run in the pane (e.g. ``claude``).
            When provided, the pane launches this command instead of a bare shell.
        deploy_spec_kwargs: If provided, deploy Ace config files (CLAUDE.md,
            hooks, settings.json) using the real session_id after DB creation.
    """
    # Step 1: DB row first — guarantees the UI always sees every entity
    project = await db_ops.get_project(conn, project_id)
    provider_cfg = getattr(getattr(conn, "_connection", None), "app_state", None)
    if provider_cfg is not None and getattr(provider_cfg, "settings", None) is not None:
        provider = provider_cfg.settings.agent_provider.default
    else:
        from atc.config import load_settings

        provider = load_settings().agent_provider.default

    session = await db_ops.create_session(
        conn,
        project_id=project_id,
        session_type="ace",
        name=name,
        provider=provider,
        scope_type="project",
        scope_id=project_id,
        task_id=task_id,
        host=host,
        status=SessionStatus.CONNECTING.value,
    )

    # Step 1b: Deploy config files with the real session_id so hooks,
    # CLAUDE.md, and heartbeat all reference the correct ID.
    effective_working_dir = working_dir
    deployed = None
    if deploy_spec_kwargs is not None:
        from atc.agents.deploy import AceDeploySpec, deploy_ace_files

        spec = AceDeploySpec(
            session_id=session.id,
            **deploy_spec_kwargs,
        )
        deployed = deploy_ace_files(spec)
        # Use the staging directory as working_dir so Claude Code finds
        # the deployed CLAUDE.md and .claude/settings.json (hooks, model).
        # The Ace can still access the repo via the repo_path in CLAUDE.md.
        effective_working_dir = str(deployed.root)

    # Step 2: publish creation event — UI shows it immediately
    if event_bus:
        await event_bus.publish(
            "session_created",
            {"session_id": session.id, "session_type": "ace", "project_id": project_id},
        )

    # Step 3: spawn tmux pane
    try:
        # Provider workspace prep (alongside existing tmux logic — fallback safe)
        if effective_working_dir:
            try:
                from atc.agents.factory import create_provider

                provider_name = provider
                _provider = create_provider(provider_name)
                await _provider.prepare_workspace(
                    session.id, working_dir=effective_working_dir
                )
            except Exception as _prep_exc:
                logger.debug(
                    "provider.prepare_workspace skipped for %s: %s",
                    session.id,
                    _prep_exc,
                )

        tmux_session, pane_id = await _spawn_provider_session(
            conn,
            session.id,
            project_id=project_id,
            session_type="ace",
            working_dir=effective_working_dir,
            context_file=deployed.claude_md_path if deployed and deployed.claude_md_path.exists() else None,
            launch_command=launch_command,
        )
        await db_ops.update_session_tmux(conn, session.id, tmux_session, pane_id)

        # Step 4a: success → idle
        await transition(session.id, SessionStatus.CONNECTING, SessionStatus.IDLE, event_bus)
        await db_ops.update_session_status(conn, session.id, SessionStatus.IDLE.value)
    except Exception as exc:
        # Step 4b: failure → error
        logger.exception("Failed to spawn tmux pane for session %s", session.id)
        await db_ops.update_session_status(conn, session.id, SessionStatus.ERROR.value)
        if event_bus:
            await event_bus.publish(
                "session_status_changed",
                {
                    "session_id": session.id,
                    "previous_status": SessionStatus.CONNECTING.value,
                    "new_status": SessionStatus.ERROR.value,
                },
            )
        raise RuntimeError(str(exc)) from exc

    return session.id


async def _send_session_instruction(
    conn: aiosqlite.Connection,
    session_id: str,
    instruction: str,
) -> bool:
    """Send an instruction through the configured provider for a session."""
    from atc.agents.factory import create_provider

    session = await db_ops.get_session(conn, session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")
    if not session.project_id:
        raise ValueError(f"Session {session_id} has no project")

    project = await db_ops.get_project(conn, session.project_id)
    provider_name = project.agent_provider if project and project.agent_provider else "claude_code"
    provider = create_provider(provider_name)
    result = await provider.send_prompt(session_id, instruction)
    return result.accepted


async def start_ace(
    conn: aiosqlite.Connection,
    session_id: str,
    *,
    instruction: str | None = None,
    event_bus: EventBus | None = None,
) -> None:
    """Start an ace session — send an instruction to its tmux pane.

    Uses atomic instruction sending with TUI readiness check and
    capture-pane verification to prevent swallowed instructions.

    Transitions: idle|waiting → working.
    """
    session = await db_ops.get_session(conn, session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    current = SessionStatus(session.status)
    await transition(session_id, current, SessionStatus.WORKING, event_bus)
    await db_ops.update_session_status(conn, session_id, SessionStatus.WORKING.value)

    if instruction:
        delivered = await _send_session_instruction(conn, session_id, instruction)
        if not delivered:
            logger.error("Session %s: instruction delivery failed, marking error", session_id)
            await transition(session_id, SessionStatus.WORKING, SessionStatus.ERROR, event_bus)
            await db_ops.update_session_status(conn, session_id, SessionStatus.ERROR.value)
            raise RuntimeError(f"Failed to deliver instruction to session {session_id}")


async def stop_ace(
    conn: aiosqlite.Connection,
    session_id: str,
    *,
    event_bus: EventBus | None = None,
) -> None:
    """Stop an ace session — transition to paused.

    Transitions: working|waiting → paused.
    """
    session = await db_ops.get_session(conn, session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    current = SessionStatus(session.status)
    await transition(session_id, current, SessionStatus.PAUSED, event_bus)
    await db_ops.update_session_status(conn, session_id, SessionStatus.PAUSED.value)


async def destroy_ace(
    conn: aiosqlite.Connection,
    session_id: str,
    *,
    event_bus: EventBus | None = None,
) -> None:
    """Destroy an ace session — kill tmux pane and delete DB row."""
    session = await db_ops.get_session(conn, session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    # Kill tmux pane if present
    if session.tmux_pane:
        await _kill_pane(session.tmux_pane)

    # Delete from DB
    await db_ops.delete_session(conn, session_id)

    if event_bus:
        await event_bus.publish(
            "session_destroyed",
            {"session_id": session_id, "session_type": "ace", "project_id": session.project_id},
        )


# ---------------------------------------------------------------------------
# Verification loop (creation reliability — design doc §10a)
# ---------------------------------------------------------------------------


@dataclass
class VerificationResult:
    """Result of a single session verification check.

    Note: only the ``alive`` phase is intended as a provider-agnostic liveness
    gate. The later ``working`` and ``progressing`` phases are output-based
    heuristics for tmux-backed sessions, not universal provider health signals.
    """

    ok: bool
    phase: str  # "alive", "working", "progressing"
    detail: str = ""


async def verify_alive(
    conn: aiosqlite.Connection,
    session_id: str,
) -> VerificationResult:
    """Check 1 (t+10s): Is the session alive?

    This is the hard, provider-agnostic verification gate used by creation
    reliability. Today it still relies on tmux-pane presence because ATC's live
    session transport is tmux-backed, but conceptually this phase is about
    session liveness, not Claude-specific behavior.

    - Session row exists with status != 'error'
    - tmux pane is alive
    """
    session = await db_ops.get_session(conn, session_id)
    if session is None:
        return VerificationResult(ok=False, phase="alive", detail="session not found in DB")

    if session.status == SessionStatus.ERROR.value:
        return VerificationResult(ok=False, phase="alive", detail="session status is error")

    if not session.tmux_pane:
        return VerificationResult(ok=False, phase="alive", detail="no tmux pane assigned")

    if not await _pane_is_alive(session.tmux_pane):
        return VerificationResult(
            ok=False, phase="alive", detail=f"tmux pane {session.tmux_pane} is dead"
        )

    return VerificationResult(ok=True, phase="alive")


async def verify_working(
    conn: aiosqlite.Connection,
    session_id: str,
) -> VerificationResult:
    """Check 2 (t+60s): Is there evidence of output activity?

    This is a soft heuristic for tmux-backed sessions, not a universal provider
    readiness contract. A session can be healthy without satisfying these exact
    output expectations once ATC supports non-tmux or differently-behaving
    providers.

    Current signals:
    - Status has transitioned from idle → working or waiting
    - Pane output is non-empty (scrollback has content)
    """
    session = await db_ops.get_session(conn, session_id)
    if session is None:
        return VerificationResult(ok=False, phase="working", detail="session not found in DB")

    active_statuses = {
        SessionStatus.WORKING.value,
        SessionStatus.WAITING.value,
    }
    if session.status in active_statuses:
        return VerificationResult(ok=True, phase="working")

    # Check if pane has any output (sign of activity)
    if session.tmux_pane:
        try:
            output = await _capture_pane(session.tmux_pane)
            if output.strip():
                return VerificationResult(ok=True, phase="working", detail="pane has output")
        except RuntimeError:
            pass

    return VerificationResult(
        ok=False,
        phase="working",
        detail=f"session still in status '{session.status}', no activity detected",
    )


async def verify_progressing(
    conn: aiosqlite.Connection,
    session_id: str,
    *,
    previous_output: str = "",
) -> VerificationResult:
    """Check 3 (t+120s): Is tmux output still changing?

    This is another soft heuristic for tmux-backed sessions. It should be read
    as "output appears to be progressing" rather than a generic provider-level
    guarantee that useful work is happening.

    Current signals:
    - Pane output has changed since last check
    - No common error patterns in output
    """
    session = await db_ops.get_session(conn, session_id)
    if session is None:
        return VerificationResult(ok=False, phase="progressing", detail="session not found in DB")

    if session.status == SessionStatus.ERROR.value:
        return VerificationResult(ok=False, phase="progressing", detail="session status is error")

    if not session.tmux_pane:
        return VerificationResult(ok=False, phase="progressing", detail="no tmux pane")

    try:
        output = await _capture_pane(session.tmux_pane, lines=100)
    except RuntimeError:
        return VerificationResult(ok=False, phase="progressing", detail="capture-pane failed")

    # Check output has changed since last check
    if previous_output and output.strip() == previous_output.strip():
        return VerificationResult(
            ok=False, phase="progressing", detail="pane output unchanged since last check"
        )

    # Check for common error patterns
    error_patterns = ["Traceback (most recent call last)", "permission denied", "FATAL"]
    for pattern in error_patterns:
        if pattern.lower() in output.lower():
            return VerificationResult(
                ok=False,
                phase="progressing",
                detail=f"error pattern detected: {pattern}",
            )

    return VerificationResult(ok=True, phase="progressing")


async def schedule_verification(
    conn: aiosqlite.Connection,
    session_id: str,
    created_by: str,
    *,
    event_bus: EventBus | None = None,
) -> None:
    """Schedule the three-phase verification loop after entity creation.

    Runs as a background task:
      - t+10s:  hard liveness check
      - t+60s:  soft output-activity check
      - t+120s: soft output-progress check

    Only the first phase is treated as a hard creation gate. Later phases are
    tmux-output heuristics that help surface likely stalls for current providers.
    """

    async def _run_checks() -> None:
        # --- Check 1: t+10s — Alive? ---
        await asyncio.sleep(10)
        result = await verify_alive(conn, session_id)
        if not result.ok:
            logger.error("Session %s: alive check failed (%s)", session_id, result.detail)
            await _handle_creation_failure(conn, session_id, event_bus)
            return  # no point checking further

        # --- Check 2: t+60s — Working? ---
        await asyncio.sleep(50)  # 60s total
        result = await verify_working(conn, session_id)
        if not result.ok:
            logger.warning(
                "Session %s: output-activity check failed (%s), may need attention",
                session_id,
                result.detail,
            )
            if event_bus:
                await event_bus.publish(
                    "session_verification_failed",
                    {
                        "session_id": session_id,
                        "phase": "working",
                        "detail": result.detail,
                        "created_by": created_by,
                    },
                )

        # Capture output for progress comparison
        mid_output = ""
        session = await db_ops.get_session(conn, session_id)
        if session and session.tmux_pane:
            with contextlib.suppress(RuntimeError):
                mid_output = await _capture_pane(session.tmux_pane, lines=100)

        # --- Check 3: t+120s — Progressing? ---
        await asyncio.sleep(60)  # 120s total
        result = await verify_progressing(conn, session_id, previous_output=mid_output)
        if not result.ok:
            logger.warning("Session %s: output-progress check failed (%s)", session_id, result.detail)
            await _handle_stalled(conn, session_id, event_bus)

    asyncio.create_task(_run_checks())


async def _handle_creation_failure(
    conn: aiosqlite.Connection,
    session_id: str,
    event_bus: EventBus | None = None,
) -> None:
    """Handle a session that failed the alive check."""
    session = await db_ops.get_session(conn, session_id)
    if session is None:
        return

    current = SessionStatus(session.status)
    if current != SessionStatus.ERROR:
        try:
            await transition(session_id, current, SessionStatus.ERROR, event_bus)
        except Exception:
            logger.warning("Could not transition %s to error from %s", session_id, current)
        await db_ops.update_session_status(conn, session_id, SessionStatus.ERROR.value)

    if event_bus:
        await event_bus.publish(
            "session_creation_failed",
            {"session_id": session_id, "phase": "alive"},
        )


async def _handle_stalled(
    conn: aiosqlite.Connection,
    session_id: str,
    event_bus: EventBus | None = None,
) -> None:
    """Handle a session that failed the progress check — mark as stalled.

    Note: 'stalled' is not a formal state in the state machine; we use
    WAITING to indicate the session needs attention, and publish an event
    so the creating entity or UI can surface a warning.
    """
    session = await db_ops.get_session(conn, session_id)
    if session is None:
        return

    if event_bus:
        await event_bus.publish(
            "session_stalled",
            {"session_id": session_id, "phase": "progressing"},
        )


# ---------------------------------------------------------------------------
# Legacy single-shot verify (kept for backward compat)
# ---------------------------------------------------------------------------


async def verify_session(
    conn: aiosqlite.Connection,
    session_id: str,
    *,
    event_bus: EventBus | None = None,
) -> bool:
    """Run a single alive-check verification.

    Returns ``True`` if the session appears healthy, ``False`` otherwise.
    Called by the orchestrator after spawning.
    """
    result = await verify_alive(conn, session_id)
    if not result.ok:
        session = await db_ops.get_session(conn, session_id)
        if session and session.tmux_pane:
            logger.warning("Session %s: tmux pane %s is dead", session_id, session.tmux_pane)
            try:
                await transition(
                    session_id,
                    SessionStatus(session.status),
                    SessionStatus.DISCONNECTED,
                    event_bus,
                )
                await db_ops.update_session_status(
                    conn, session_id, SessionStatus.DISCONNECTED.value
                )
            except Exception:
                logger.warning("Could not transition %s to disconnected", session_id)
        return False
    return True

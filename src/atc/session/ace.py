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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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

# TUI readiness: max seconds to wait for alternate_on == False
TUI_READY_TIMEOUT = 10.0
TUI_READY_POLL_INTERVAL = 0.5

# Instruction verification: seconds to wait before capture-pane check
INSTRUCTION_VERIFY_DELAY = 2.0
INSTRUCTION_MAX_RETRIES = 3

# Strings that indicate a known startup dialog is on screen.
# Used to guard against false-positive "Claude is running" detection —
# the trust dialog body itself contains "Claude Code will be able to..."
# which would otherwise trigger the early-exit before any dialog is handled.
_DIALOG_TRIGGERS: tuple[str, ...] = (
    "enter to confirm",
    "trust this folder",
    "do you trust",
    "bypass permissions",
    "do you want to use this api key",
    "will be able to read",
    "yes, i trust this folder",
    "no, exit",
    "security guide",
    # Welcome/tips screen — Claude is running but not yet in the interactive
    # prompt.  Listed here so the "claude code" fast-exit doesn't fire early.
    "tips for getting started",
    "welcome to claude code",
    "welcome back",
)


# ---------------------------------------------------------------------------
# tmux helpers (thin wrappers around subprocess)
# ---------------------------------------------------------------------------


async def _tmux_run(*args: str) -> str:
    """Run a tmux command and return stdout."""
    proc = await asyncio.create_subprocess_exec(
        "tmux",
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

        from atc.agents.auth import resolve_agent_api_key

        key = resolve_agent_api_key()
        if key:
            command = f"ANTHROPIC_API_KEY={_shlex.quote(key)} {command}"
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
    """Accept all Claude Code startup confirmation dialogs.

    Claude Code (v2+) shows up to two dialogs before becoming interactive:

    Dialog 1 — API key selector (when ANTHROPIC_API_KEY is set):
        "Detected a custom API key in your environment"
        "Do you want to use this API key?"
        ❯ 2. No (recommended)  ← default cursor position
        → Send Enter to dismiss (keeps OAuth login, ignores the env key)

    Dialog 2 — Bypass permissions confirmation (with --dangerously-skip-permissions):
        "By proceeding, you accept all responsibility..."
        ❯ 1. No, exit          ← default cursor position
          2. Yes, I accept
        → Send Down then Enter to select "Yes, I accept"

    Legacy dialog — trust this folder:
        → Send Enter to accept

    Returns True if any dialog was detected and dismissed, False if Claude
    started without showing any dialog within *timeout* seconds.
    """
    from atc.agents.auth import is_oauth_key, resolve_agent_api_key

    _key = resolve_agent_api_key()
    _use_api_key = bool(_key and not is_oauth_key(_key))

    poll_interval = 0.5
    elapsed = 0.0
    # Track which dialog types we've already handled to avoid re-triggering on
    # the same pane output while waiting for the screen to clear.
    dismissed: set[str] = set()

    while elapsed < timeout:
        try:
            output = await _capture_pane(pane_id)
            lowered = output.lower()

            # Dialog 1: API key selector — Enter dismisses (selects "No")
            if "api_key_selector" not in dismissed and (
                "do you want to use this api key" in lowered
                or ("detected" in lowered and "api key" in lowered)
            ):
                if _use_api_key:
                    # Option 1 "Yes" is above the pre-selected option 2 "No"
                    await _tmux_run("send-keys", "-t", pane_id, "Up")
                    await asyncio.sleep(0.1)
                    logger.info("Pane %s: accepted API key selector (real key configured)", pane_id)
                else:
                    logger.info("Pane %s: dismissed API key selector dialog (OAuth mode)", pane_id)
                await _tmux_run("send-keys", "-t", pane_id, "Enter")
                dismissed.add("api_key_selector")
                await asyncio.sleep(1.0)  # wait for next dialog to appear
                continue

            # Dialog 2: bypass permissions confirmation — Down then Enter selects "Yes"
            if "bypass_permissions" not in dismissed and (
                "bypass permissions" in lowered
                or ("yes, i accept" in lowered and "no, exit" in lowered)
            ):
                await _tmux_run("send-keys", "-t", pane_id, "Down")
                await asyncio.sleep(0.1)
                await _tmux_run("send-keys", "-t", pane_id, "Enter")
                logger.info("Pane %s: accepted bypass permissions dialog", pane_id)
                dismissed.add("bypass_permissions")
                await asyncio.sleep(1.0)
                continue

            # Dialog 3: security guide / trust-folder (new variant, v2+)
            #   Body: "Claude Code will be able to read, edit, and execute files here."
            #   ❯ 1. Yes, I trust this folder   ← pre-selected
            #     2. No, exit
            #   Enter to confirm · Esc to cancel
            # Option 1 is pre-selected — Enter accepts without arrow keys.
            if "trust_folder" not in dismissed and (
                "trust this folder" in lowered
                or "do you trust" in lowered
                or "yes, i trust this folder" in lowered
                or "will be able to read" in lowered
            ):
                await _tmux_run("send-keys", "-t", pane_id, "Enter")
                logger.info("Pane %s: accepted trust-folder dialog", pane_id)
                dismissed.add("trust_folder")
                await asyncio.sleep(1.0)
                continue

            # Claude Code TUI is running — all dialogs cleared.
            # NOTE: must run AFTER dialog checks. The trust dialog body text
            # contains "Claude Code will be able to..." which would cause a
            # false-positive early-exit if this check ran first.
            # Guard: only exit early when no known dialog trigger strings present.
            if "claude code" in lowered and not any(t in lowered for t in _DIALOG_TRIGGERS):
                logger.debug("Pane %s: Claude Code ready (text match, %.1fs)", pane_id, elapsed)
                return bool(dismissed)

            # Fast-exit: pane left alternate-screen mode (TUI exited) and the
            # interactive ❯ prompt is visible.  This fires on Mac where the
            # alternate_on flag goes False as soon as dialogs clear.
            try:
                alt_on = await _get_alternate_on(pane_id)
                if not alt_on and ("❯" in output or "> " in output):
                    logger.debug(
                        "Pane %s: prompt visible (alternate_on=0, %.1fs)", pane_id, elapsed
                    )
                    return bool(dismissed)
            except RuntimeError:
                pass

        except RuntimeError:
            pass
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    logger.debug("Pane %s: dialog handling finished after %.1fs", pane_id, timeout)
    return bool(dismissed)


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


# ---------------------------------------------------------------------------
# TUI readiness check
# ---------------------------------------------------------------------------


async def wait_for_prompt(
    pane_id: str,
    *,
    timeout: float = 10.0,
    poll_interval: float = 0.5,
) -> bool:
    """Wait until the pane shows an empty prompt with alternate_on == 0.

    Checks two conditions simultaneously:
    1. alternate_on == 0 (no full-screen TUI active)
    2. A bare prompt line: starts with '❯' or '> ' with nothing after it

    Returns True when both conditions are met within *timeout* seconds,
    False otherwise.
    """
    import re as _re

    _prompt_re = _re.compile(r"^[❯>]\s*$", _re.MULTILINE)

    elapsed = 0.0
    while elapsed < timeout:
        try:
            alt_on = await _get_alternate_on(pane_id)
            if not alt_on:
                output = await _capture_pane(pane_id)
                if _prompt_re.search(output):
                    return True
        except RuntimeError:
            return False
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    logger.warning(
        "Pane %s: prompt not ready after %.1fs (wait_for_prompt timed out)",
        pane_id,
        timeout,
    )
    return False


async def check_tui_ready(
    pane_id: str,
    *,
    timeout: float = TUI_READY_TIMEOUT,
    poll_interval: float = TUI_READY_POLL_INTERVAL,
) -> bool:
    """Wait until the pane exits alternate screen mode (TUI not active).

    Returns ``True`` if the pane is ready for input within *timeout* seconds,
    ``False`` if it timed out (TUI still active).
    """
    elapsed = 0.0
    while elapsed < timeout:
        try:
            if not await _get_alternate_on(pane_id):
                return True
        except RuntimeError:
            # Pane may have died
            return False
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    logger.warning(
        "Pane %s: TUI still active after %.1fs, alternate_on not cleared", pane_id, timeout
    )
    return False


# ---------------------------------------------------------------------------
# Atomic instruction sending
# ---------------------------------------------------------------------------


async def send_instruction(
    pane_id: str,
    text: str,
    *,
    verify: bool = True,
    max_retries: int = INSTRUCTION_MAX_RETRIES,
) -> bool:
    """Send instruction text + Enter atomically, with optional verification.

    1. Check TUI readiness (alternate_on == False)
    2. Send text + Enter with no await gap between them
    3. Verify instruction appears in capture-pane output

    Returns ``True`` if the instruction was verified (or verification skipped).
    """
    for attempt in range(1, max_retries + 1):
        # Step 1: TUI readiness — use prompt-ready polling for first attempt
        # to ensure the welcome screen has fully cleared before sending.
        if attempt == 1:
            ready = await wait_for_prompt(pane_id)
        else:
            ready = await check_tui_ready(pane_id)
        if not ready:
            logger.warning("Pane %s: TUI not ready on attempt %d/%d", pane_id, attempt, max_retries)
            continue

        # Step 2: Atomic send — text and Enter back-to-back, no await gap
        await _tmux_run("send-keys", "-t", pane_id, text, "Enter")

        if not verify:
            return True

        # Step 3: Verify instruction was received
        await asyncio.sleep(INSTRUCTION_VERIFY_DELAY)
        try:
            output = await _capture_pane(pane_id)
            output_lower = output.lower()

            # If the welcome/tips overlay is still visible, the instruction text
            # is hidden behind it — skip retry and treat as delivered.  The
            # overlay is purely cosmetic and does not block input; Claude is
            # already processing the instruction.
            _welcome_triggers = (
                "tips for getting started",
                "welcome to claude code",
                "welcome back",
            )
            if any(t in output_lower for t in _welcome_triggers):
                logger.info(
                    "Pane %s: welcome screen visible on attempt %d"
                    " — assuming instruction delivered",
                    pane_id,
                    attempt,
                )
                return True

            # Check if any significant portion of the instruction appears in output.
            # Use the first 80 chars as a fingerprint to avoid issues with line wrapping.
            fingerprint = text[:80].strip()
            if fingerprint and fingerprint in output:
                logger.info("Pane %s: instruction verified on attempt %d", pane_id, attempt)
                return True
            logger.warning(
                "Pane %s: instruction not found in output (attempt %d/%d)",
                pane_id,
                attempt,
                max_retries,
            )
        except RuntimeError:
            logger.warning(
                "Pane %s: capture-pane failed on attempt %d/%d",
                pane_id,
                attempt,
                max_retries,
            )

    logger.error("Pane %s: instruction delivery failed after %d attempts", pane_id, max_retries)
    return False


# Legacy wrapper kept for backward compatibility with existing callers
async def _send_keys(pane_id: str, keys: str) -> None:
    """Send keystrokes to a tmux pane (atomic: text + Enter in one call)."""
    await _tmux_run("send-keys", "-t", pane_id, keys, "Enter")


# ---------------------------------------------------------------------------
# Ace lifecycle
# ---------------------------------------------------------------------------


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
    session = await db_ops.create_session(
        conn,
        project_id=project_id,
        session_type="ace",
        name=name,
        task_id=task_id,
        host=host,
        status=SessionStatus.CONNECTING.value,
    )

    # Step 1b: Deploy config files with the real session_id so hooks,
    # CLAUDE.md, and heartbeat all reference the correct ID.
    effective_working_dir = working_dir
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

                _provider = create_provider("claude_code")
                await _provider.prepare_workspace(
                    session.id, working_dir=effective_working_dir
                )
            except Exception as _prep_exc:
                logger.debug(
                    "provider.prepare_workspace skipped for %s: %s",
                    session.id,
                    _prep_exc,
                )

        await _ensure_tmux_session(ATC_TMUX_SESSION)
        pane_id = await _spawn_pane(
            ATC_TMUX_SESSION,
            launch_command,
            working_dir=effective_working_dir,
        )
        await _accept_trust_dialog(pane_id)
        await db_ops.update_session_tmux(conn, session.id, ATC_TMUX_SESSION, pane_id)

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

    if instruction and session.tmux_pane:
        delivered = await send_instruction(session.tmux_pane, instruction)
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
    """Result of a single verification check."""

    ok: bool
    phase: str  # "alive", "working", "progressing"
    detail: str = ""


async def verify_alive(
    conn: aiosqlite.Connection,
    session_id: str,
) -> VerificationResult:
    """Check 1 (t+10s): Is the session alive?

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
    """Check 2 (t+60s): Did the session start working?

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
    """Check 3 (t+120s): Is the session making progress?

    - Pane output has changed since last check
    - No error patterns in output
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
      - t+10s:  alive check
      - t+60s:  working check (re-sends instruction on failure)
      - t+120s: progressing check (marks stalled on failure)
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
                "Session %s: working check failed (%s), may need instruction re-send",
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
            logger.warning("Session %s: progress check failed (%s)", session_id, result.detail)
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

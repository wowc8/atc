"""Claude-specific runtime helpers for tmux-backed sessions.

This module holds startup-dialog handling, readiness checks, and
instruction-delivery verification that are specific to Claude Code's TUI.
Shared session orchestration should call into this module rather than
embedding Claude-specific behavior directly.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Final

from atc.terminal.control import send_instruction_async

logger = logging.getLogger(__name__)

# Default tmux session name used for ATC panes
ATC_TMUX_SESSION: Final[str] = "atc"

# TUI readiness: max seconds to wait for alternate_on == False
TUI_READY_TIMEOUT: Final[float] = 10.0
TUI_READY_POLL_INTERVAL: Final[float] = 0.5

# Instruction verification: seconds to wait before capture-pane check
INSTRUCTION_VERIFY_DELAY: Final[float] = 2.0
INSTRUCTION_MAX_RETRIES: Final[int] = 3

# Strings that indicate a known startup dialog is on screen.
_DIALOG_TRIGGERS: Final[tuple[str, ...]] = (
    "enter to confirm",
    "trust this folder",
    "do you trust",
    "bypass permissions",
    "bypass permissions on",
    "yes, i accept",
    "do you want to use this api key",
    "will be able to read",
    "'ll be able to read",
    "able to read, edit",
    "yes, i trust this folder",
    "no, exit",
    "security guide",
    "is this a project you created",
    "one you trust",
    "tips for getting started",
    "welcome to claude code",
    "welcome back",
)

_WELCOME_TRIGGERS: Final[tuple[str, ...]] = (
    "tips for getting started",
    "welcome to claude code",
    "welcome back",
)

_BARE_PROMPT_RE: Final[re.Pattern[str]] = re.compile(r"^[❯>]\s*$", re.MULTILINE)


async def accept_startup_dialogs(
    pane_id: str,
    *,
    capture_pane,
    get_alternate_on,
    tmux_run,
    timeout: float = 10.0,
) -> bool:
    """Accept Claude Code startup confirmation dialogs for a tmux pane."""
    from atc.agents.auth import is_oauth_key, resolve_agent_api_key

    key = resolve_agent_api_key()
    use_api_key = bool(key and not is_oauth_key(key))

    poll_interval = 0.5
    elapsed = 0.0
    dismissed: set[str] = set()

    while elapsed < timeout:
        try:
            output = await capture_pane(pane_id)
            lowered = output.lower()

            if "api_key_selector" not in dismissed and (
                "do you want to use this api key" in lowered
                or ("detected" in lowered and "api key" in lowered)
            ):
                if use_api_key:
                    await tmux_run("send-keys", "-t", pane_id, "Up")
                    await asyncio.sleep(0.1)
                    logger.info("Pane %s: accepted API key selector (real key configured)", pane_id)
                else:
                    logger.info("Pane %s: dismissed API key selector dialog (OAuth mode)", pane_id)
                await tmux_run("send-keys", "-t", pane_id, "Enter")
                dismissed.add("api_key_selector")
                await asyncio.sleep(1.0)
                continue

            if "bypass_permissions" not in dismissed and (
                "bypass permissions" in lowered
                or ("yes, i accept" in lowered and "no, exit" in lowered)
            ):
                await tmux_run("send-keys", "-t", pane_id, "Down")
                await asyncio.sleep(0.1)
                await tmux_run("send-keys", "-t", pane_id, "Enter")
                logger.info("Pane %s: accepted bypass permissions dialog", pane_id)
                dismissed.add("bypass_permissions")
                await asyncio.sleep(1.0)
                continue

            if "trust_folder" not in dismissed and (
                "trust this folder" in lowered
                or "do you trust" in lowered
                or "yes, i trust this folder" in lowered
                or "will be able to read" in lowered
                or "'ll be able to read" in lowered
                or "able to read, edit" in lowered
                or "is this a project you created" in lowered
            ):
                await tmux_run("send-keys", "-t", pane_id, "Enter")
                logger.info("Pane %s: accepted trust-folder dialog", pane_id)
                dismissed.add("trust_folder")
                await asyncio.sleep(1.0)
                continue

            if "claude code" in lowered and not any(t in lowered for t in _DIALOG_TRIGGERS):
                logger.debug("Pane %s: Claude Code ready (text match, %.1fs)", pane_id, elapsed)
                return bool(dismissed)

            try:
                alt_on = await get_alternate_on(pane_id)
                if (
                    not alt_on
                    and _BARE_PROMPT_RE.search(output)
                    and not any(t in lowered for t in _DIALOG_TRIGGERS)
                ):
                    logger.debug(
                        "Pane %s: prompt visible (alternate_on=0, %.1fs)",
                        pane_id,
                        elapsed,
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


async def wait_for_prompt(
    pane_id: str,
    *,
    get_alternate_on,
    capture_pane,
    timeout: float = 10.0,
    poll_interval: float = 0.5,
) -> bool:
    """Wait until the pane shows an empty Claude prompt with alternate_on == 0."""
    elapsed = 0.0
    while elapsed < timeout:
        try:
            alt_on = await get_alternate_on(pane_id)
            if not alt_on:
                output = await capture_pane(pane_id)
                lowered = output.lower()
                if any(trigger in lowered for trigger in _DIALOG_TRIGGERS):
                    logger.debug(
                        "Pane %s: prompt suppressed because startup dialog is still visible",
                        pane_id,
                    )
                elif _BARE_PROMPT_RE.search(output):
                    return True
        except RuntimeError:
            return False
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    logger.warning(
        "Pane %s: prompt not ready after %.1fs (wait_for_prompt timed out)", pane_id, timeout
    )
    return False


async def check_tui_ready(
    pane_id: str,
    *,
    get_alternate_on,
    timeout: float = TUI_READY_TIMEOUT,
    poll_interval: float = TUI_READY_POLL_INTERVAL,
) -> bool:
    """Wait until the Claude pane exits alternate screen mode."""
    elapsed = 0.0
    while elapsed < timeout:
        try:
            if not await get_alternate_on(pane_id):
                return True
        except RuntimeError:
            return False
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    logger.warning(
        "Pane %s: TUI still active after %.1fs, alternate_on not cleared", pane_id, timeout
    )
    return False


async def send_instruction(
    pane_id: str,
    text: str,
    *,
    capture_pane,
    pane_is_alive,
    wait_for_prompt_fn,
    check_tui_ready_fn,
    verify: bool = True,
    max_retries: int = INSTRUCTION_MAX_RETRIES,
) -> bool:
    """Send instruction text + Enter atomically, with Claude-specific verification."""
    for attempt in range(1, max_retries + 1):
        if attempt == 1:
            ready = await wait_for_prompt_fn(pane_id)
        else:
            ready = await check_tui_ready_fn(pane_id)
        if not ready:
            logger.warning("Pane %s: TUI not ready on attempt %d/%d", pane_id, attempt, max_retries)
            continue

        await send_instruction_async(ATC_TMUX_SESSION, pane_id, text)

        if not verify:
            return True

        await asyncio.sleep(INSTRUCTION_VERIFY_DELAY)
        try:
            output = await capture_pane(pane_id)
            output_lower = output.lower()

            if any(t in output_lower for t in _WELCOME_TRIGGERS):
                logger.info(
                    "Pane %s: welcome screen visible on attempt %d — assuming "
                    "instruction delivered",
                    pane_id,
                    attempt,
                )
                return True

            fingerprint = text[:80].strip()
            if fingerprint and fingerprint in output:
                logger.info("Pane %s: instruction verified on attempt %d", pane_id, attempt)
                return True

            if not await wait_for_prompt_fn(pane_id, timeout=1.0, poll_interval=0.25):
                latest_output = await capture_pane(pane_id)
                latest_lower = latest_output.lower()
                if any(trigger in latest_lower for trigger in _DIALOG_TRIGGERS):
                    logger.warning(
                        "Pane %s: startup dialog still visible after send on attempt %d",
                        pane_id,
                        attempt,
                    )
                elif await pane_is_alive(pane_id):
                    logger.info(
                        "Pane %s: prompt disappeared after send on attempt %d — "
                        "assuming instruction accepted",
                        pane_id,
                        attempt,
                    )
                    return True
                else:
                    logger.warning(
                        "Pane %s: prompt disappeared after send on attempt %d but pane is dead",
                        pane_id,
                        attempt,
                    )
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

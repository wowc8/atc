"""Shared tmux substrate helpers for the runtime/provider refactor."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from atc.runtime.errors import RuntimeInvocationError


def resolve_tmux_binary() -> str:
    """Resolve the tmux binary path consistently across environments."""

    tmux = shutil.which("tmux")
    if tmux:
        return tmux
    for candidate in ("/usr/local/bin/tmux", "/opt/homebrew/bin/tmux", "/usr/bin/tmux"):
        if shutil.which(candidate):
            return candidate
    return "tmux"


async def run_tmux(*args: str) -> str:
    """Run a tmux command and return stdout, raising a typed runtime error on failure."""

    proc = await asyncio.create_subprocess_exec(
        resolve_tmux_binary(),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeInvocationError(
            f"tmux {' '.join(args)} failed: {stderr.decode().strip()}"
        )
    return stdout.decode().strip()


async def ensure_tmux_session(session_name: str) -> None:
    """Ensure a detached tmux session exists with sane default dimensions."""

    try:
        await run_tmux("has-session", "-t", session_name)
    except RuntimeInvocationError:
        await run_tmux("new-session", "-d", "-s", session_name, "-x", "120", "-y", "40")


async def spawn_window_pane(
    session_name: str,
    command: str | None = None,
    *,
    working_dir: str | None = None,
) -> str:
    """Create a new window in a tmux session and return its pane id."""

    args = ["new-window", "-a", "-t", session_name, "-d", "-P", "-F", "#{pane_id}"]
    if working_dir:
        if not os.path.isdir(working_dir):
            os.makedirs(working_dir, exist_ok=True)
        args.extend(["-c", working_dir])
    if command:
        args.append(command)
    return await run_tmux(*args)


async def kill_pane(pane_id: str) -> None:
    """Kill a tmux pane by pane id."""

    await run_tmux("kill-pane", "-t", pane_id)


async def capture_pane_text(pane_id: str, *, lines: int = 50) -> str:
    """Capture the last visible lines from a tmux pane."""

    return await run_tmux("capture-pane", "-t", pane_id, "-p", "-S", f"-{lines}")


async def pane_exists(pane_id: str) -> bool:
    """Return True if the target tmux pane still exists."""

    try:
        await run_tmux("has-session", "-t", pane_id)
        return True
    except RuntimeInvocationError:
        return False


async def alternate_screen_active(pane_id: str) -> bool:
    """Return True when the pane is in alternate-screen/TUI mode."""

    result = await run_tmux("display-message", "-t", pane_id, "-p", "#{alternate_on}")
    return result.strip() == "1"


def build_path_env_prefix(*, current_path: str | None = None, home: str | None = None) -> str:
    """Build a PATH=... shell prefix that enriches PATH for tmux pane commands."""

    current_path = current_path if current_path is not None else os.environ.get("PATH", "")
    home = home if home is not None else str(Path.home())
    candidates = [
        "/usr/local/bin",
        "/opt/homebrew/bin",
        f"{home}/.nvm/current/bin",
        f"{home}/.volta/bin",
        f"{home}/.asdf/shims",
        f"{home}/.fnm/current/bin",
    ]
    extras = [path for path in candidates if os.path.isdir(path) and path not in current_path]
    if not extras:
        return ""
    enriched = ":".join(extras) + ":" + current_path
    return f"PATH={enriched!s} "

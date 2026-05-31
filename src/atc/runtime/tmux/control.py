"""Thin compatibility bridge to the existing tmux control-mode helpers.

This module provides the first delivery primitive on the new runtime boundary
without reimplementing the existing low-level control-mode stack all at once.
"""

from __future__ import annotations

from atc.terminal.control import send_instruction_async as _legacy_send_instruction_async


async def send_bracketed_instruction(tmux_session: str, pane_id: str, text: str) -> None:
    """Send bracketed-paste instruction text followed by Enter to a tmux pane."""

    await _legacy_send_instruction_async(tmux_session, pane_id, text)

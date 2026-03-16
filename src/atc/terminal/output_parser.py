"""Regex-based TUI state detection and prompt parsing for tmux PTY output.

Detects Claude Code TUI states, shell prompts, and status transitions
from raw terminal output captured via tmux pipe-pane.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class TuiState(StrEnum):
    """Detected state of the terminal session."""

    SHELL_PROMPT = "shell_prompt"
    CLAUDE_IDLE = "claude_idle"        # Claude TUI visible, waiting for input
    CLAUDE_WORKING = "claude_working"  # Claude is generating / using tools
    CLAUDE_WAITING = "claude_waiting"  # Claude finished, waiting for user
    ALTERNATE_SCREEN = "alternate_screen"  # Full-screen TUI active (e.g. vim)
    UNKNOWN = "unknown"


@dataclass
class ParseResult:
    """Result of parsing a chunk of terminal output."""

    state: TuiState = TuiState.UNKNOWN
    prompt_detected: bool = False
    prompt_text: str = ""
    alternate_on: bool = False
    cost_dollars: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    tool_name: str | None = None
    error_text: str | None = None


# --- Pattern constants ---

# Shell prompt patterns (bash/zsh)
_SHELL_PROMPT_RE = re.compile(
    r"(?:^|\n)"            # start of line
    r"(?:\x1b\[[^m]*m)*"   # optional ANSI escapes
    r"(?:"
    r"[\w@.\-]+[#$%>]\s*"  # user@host$ or similar
    r"|"
    r"\$ \s*"              # bare dollar prompt
    r")"
    r"$",                  # at end of chunk
    re.MULTILINE,
)

# Claude Code TUI markers
_CLAUDE_IDLE_RE = re.compile(
    r"(?:"
    r"Type a message"       # Claude idle prompt text
    r"|"
    r"What would you like"  # alternate idle phrasing
    r"|"
    r">\s*$"               # simple > prompt from Claude
    r")",
    re.IGNORECASE,
)

_CLAUDE_WORKING_PATTERNS = [
    re.compile(r"⠋|⠙|⠹|⠸|⠼|⠴|⠦|⠧|⠇|⠏"),  # spinner characters
    re.compile(r"Thinking|Working|Reading|Writing|Searching|Running", re.IGNORECASE),
    re.compile(r"Tool:?\s+\w+", re.IGNORECASE),
    re.compile(r"━+"),  # progress bar
]

_CLAUDE_WAITING_RE = re.compile(
    r"(?:"
    r"Do you want to proceed"
    r"|"
    r"Yes\s*/\s*No"
    r"|"
    r"\[Y/n\]"
    r"|"
    r"Would you like to (?:proceed|approve|confirm|accept)"
    r"|"
    r"Press Enter"
    r")",
    re.IGNORECASE,
)

# Cost line from Claude Code output: e.g. "Cost: $0.12 | Tokens: 1.2k in, 0.8k out"
_COST_RE = re.compile(
    r"\$(\d+\.?\d*)"
    r".*?"
    r"(\d+(?:\.\d+)?)\s*k?\s*in"
    r".*?"
    r"(\d+(?:\.\d+)?)\s*k?\s*out",
    re.IGNORECASE,
)

# Tool usage detection
_TOOL_RE = re.compile(
    r"(?:Tool|Using|Running):\s*(\w[\w\-]*)",
    re.IGNORECASE,
)

# Error patterns
_ERROR_RE = re.compile(
    r"(?:Error|ERROR|FATAL|Traceback|panic)[:.\s](.{1,200})",
)

# Alternate screen control sequences
_ALT_SCREEN_ON = re.compile(r"\x1b\[\?1049h|\x1b\[\?47h")
_ALT_SCREEN_OFF = re.compile(r"\x1b\[\?1049l|\x1b\[\?47l")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07", "", text)


class OutputParser:
    """Stateful parser that tracks TUI state across successive output chunks.

    Feed chunks of raw terminal output via ``feed()`` and inspect the
    returned ``ParseResult`` to determine the current terminal state.
    """

    def __init__(self) -> None:
        self._alternate_on: bool = False
        self._last_state: TuiState = TuiState.UNKNOWN
        self._buffer: str = ""
        self._max_buffer: int = 8192

    @property
    def alternate_on(self) -> bool:
        """Whether the terminal is currently in alternate screen mode."""
        return self._alternate_on

    @property
    def last_state(self) -> TuiState:
        """The most recently detected TUI state."""
        return self._last_state

    def reset(self) -> None:
        """Reset parser state."""
        self._alternate_on = False
        self._last_state = TuiState.UNKNOWN
        self._buffer = ""

    def feed(self, data: str | bytes) -> ParseResult:
        """Parse a chunk of terminal output and return detected state.

        Args:
            data: Raw terminal output (str or bytes).

        Returns:
            ParseResult with detected state and extracted metadata.
        """
        if isinstance(data, bytes):
            try:
                text = data.decode("utf-8", errors="replace")
            except Exception:
                text = data.decode("latin-1")
        else:
            text = data

        result = ParseResult()

        # Track alternate screen transitions
        if _ALT_SCREEN_ON.search(text):
            self._alternate_on = True
        if _ALT_SCREEN_OFF.search(text):
            self._alternate_on = False
        result.alternate_on = self._alternate_on

        if self._alternate_on:
            result.state = TuiState.ALTERNATE_SCREEN
            self._last_state = result.state
            return result

        # Work on ANSI-stripped text for pattern matching
        clean = strip_ansi(text)

        # Append to rolling buffer for multi-chunk detection
        self._buffer += clean
        if len(self._buffer) > self._max_buffer:
            self._buffer = self._buffer[-self._max_buffer:]

        # Extract cost info
        cost_match = _COST_RE.search(clean)
        if cost_match:
            result.cost_dollars = float(cost_match.group(1))
            raw_in = float(cost_match.group(2))
            raw_out = float(cost_match.group(3))
            # Handle "k" suffix — if < 100, assume it was in thousands
            if "k" in cost_match.group(0).lower():
                result.tokens_in = int(raw_in * 1000)
                result.tokens_out = int(raw_out * 1000)
            else:
                result.tokens_in = int(raw_in)
                result.tokens_out = int(raw_out)

        # Extract tool usage
        tool_match = _TOOL_RE.search(clean)
        if tool_match:
            result.tool_name = tool_match.group(1)

        # Extract errors
        error_match = _ERROR_RE.search(clean)
        if error_match:
            result.error_text = error_match.group(1).strip()

        # Determine TUI state (order matters — most specific first)
        state = self._detect_state(clean)
        result.state = state
        self._last_state = state

        # Detect shell prompt
        if state == TuiState.SHELL_PROMPT:
            result.prompt_detected = True
            prompt_match = _SHELL_PROMPT_RE.search(clean)
            if prompt_match:
                result.prompt_text = prompt_match.group(0).strip()

        return result

    def _detect_state(self, clean: str) -> TuiState:
        """Determine TUI state from ANSI-stripped text."""
        # Check for Claude waiting (user action needed)
        if _CLAUDE_WAITING_RE.search(clean):
            return TuiState.CLAUDE_WAITING

        # Check for Claude working (spinner, tool use, etc.)
        for pattern in _CLAUDE_WORKING_PATTERNS:
            if pattern.search(clean):
                return TuiState.CLAUDE_WORKING

        # Check for Claude idle (ready for input)
        if _CLAUDE_IDLE_RE.search(clean):
            return TuiState.CLAUDE_IDLE

        # Check for shell prompt
        if _SHELL_PROMPT_RE.search(clean):
            return TuiState.SHELL_PROMPT

        # Fall back to last known state if nothing matches
        return self._last_state if self._last_state != TuiState.UNKNOWN else TuiState.UNKNOWN

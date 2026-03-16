"""Unit tests for OutputParser — TUI state detection and prompt parsing."""

from __future__ import annotations

from atc.terminal.output_parser import OutputParser, ParseResult, TuiState, strip_ansi

# ---------------------------------------------------------------------------
# strip_ansi
# ---------------------------------------------------------------------------

class TestStripAnsi:
    def test_plain_text(self) -> None:
        assert strip_ansi("hello world") == "hello world"

    def test_removes_color_codes(self) -> None:
        assert strip_ansi("\x1b[32mgreen\x1b[0m") == "green"

    def test_removes_bold(self) -> None:
        assert strip_ansi("\x1b[1mbold\x1b[0m") == "bold"

    def test_removes_osc_sequences(self) -> None:
        assert strip_ansi("\x1b]0;title\x07text") == "text"

    def test_empty_string(self) -> None:
        assert strip_ansi("") == ""

    def test_multiple_sequences(self) -> None:
        result = strip_ansi("\x1b[31mred\x1b[0m and \x1b[34mblue\x1b[0m")
        assert result == "red and blue"


# ---------------------------------------------------------------------------
# TuiState enum
# ---------------------------------------------------------------------------

class TestTuiState:
    def test_values(self) -> None:
        assert TuiState.SHELL_PROMPT == "shell_prompt"
        assert TuiState.CLAUDE_IDLE == "claude_idle"
        assert TuiState.CLAUDE_WORKING == "claude_working"
        assert TuiState.CLAUDE_WAITING == "claude_waiting"
        assert TuiState.ALTERNATE_SCREEN == "alternate_screen"
        assert TuiState.UNKNOWN == "unknown"


# ---------------------------------------------------------------------------
# OutputParser — basic state detection
# ---------------------------------------------------------------------------

class TestOutputParserStateDetection:
    def setup_method(self) -> None:
        self.parser = OutputParser()

    def test_initial_state_is_unknown(self) -> None:
        assert self.parser.last_state == TuiState.UNKNOWN
        assert not self.parser.alternate_on

    def test_shell_prompt_dollar(self) -> None:
        result = self.parser.feed("user@host$ ")
        assert result.state == TuiState.SHELL_PROMPT
        assert result.prompt_detected is True

    def test_shell_prompt_bare_dollar(self) -> None:
        result = self.parser.feed("$ ")
        assert result.state == TuiState.SHELL_PROMPT
        assert result.prompt_detected is True

    def test_shell_prompt_hash(self) -> None:
        result = self.parser.feed("root@server# ")
        assert result.state == TuiState.SHELL_PROMPT

    def test_claude_idle_type_message(self) -> None:
        result = self.parser.feed("Type a message to start chatting")
        assert result.state == TuiState.CLAUDE_IDLE

    def test_claude_idle_what_would_you_like(self) -> None:
        result = self.parser.feed("What would you like to do?")
        assert result.state == TuiState.CLAUDE_IDLE

    def test_claude_working_spinner(self) -> None:
        result = self.parser.feed("⠋ Thinking...")
        assert result.state == TuiState.CLAUDE_WORKING

    def test_claude_working_tool(self) -> None:
        result = self.parser.feed("Tool: Read /path/to/file.py")
        assert result.state == TuiState.CLAUDE_WORKING

    def test_claude_working_reading(self) -> None:
        result = self.parser.feed("Reading file contents...")
        assert result.state == TuiState.CLAUDE_WORKING

    def test_claude_working_writing(self) -> None:
        result = self.parser.feed("Writing to output.txt")
        assert result.state == TuiState.CLAUDE_WORKING

    def test_claude_working_progress_bar(self) -> None:
        result = self.parser.feed("━━━━━━━━━━")
        assert result.state == TuiState.CLAUDE_WORKING

    def test_claude_waiting_proceed(self) -> None:
        result = self.parser.feed("Do you want to proceed?")
        assert result.state == TuiState.CLAUDE_WAITING

    def test_claude_waiting_yes_no(self) -> None:
        result = self.parser.feed("Apply changes? Yes / No")
        assert result.state == TuiState.CLAUDE_WAITING

    def test_claude_waiting_yn_bracket(self) -> None:
        result = self.parser.feed("Continue? [Y/n]")
        assert result.state == TuiState.CLAUDE_WAITING

    def test_claude_waiting_would_you_like(self) -> None:
        result = self.parser.feed("Would you like to proceed with this?")
        assert result.state == TuiState.CLAUDE_WAITING

    def test_claude_waiting_press_enter(self) -> None:
        result = self.parser.feed("Press Enter to confirm")
        assert result.state == TuiState.CLAUDE_WAITING

    def test_unknown_for_random_text(self) -> None:
        result = self.parser.feed("some random output text")
        assert result.state == TuiState.UNKNOWN


# ---------------------------------------------------------------------------
# OutputParser — alternate screen tracking
# ---------------------------------------------------------------------------

class TestOutputParserAlternateScreen:
    def setup_method(self) -> None:
        self.parser = OutputParser()

    def test_alt_screen_on(self) -> None:
        result = self.parser.feed("\x1b[?1049h")
        assert result.state == TuiState.ALTERNATE_SCREEN
        assert result.alternate_on is True
        assert self.parser.alternate_on is True

    def test_alt_screen_off(self) -> None:
        self.parser.feed("\x1b[?1049h")
        result = self.parser.feed("\x1b[?1049l some prompt $ ")
        assert result.alternate_on is False
        assert self.parser.alternate_on is False

    def test_alt_screen_47h(self) -> None:
        result = self.parser.feed("\x1b[?47h")
        assert result.alternate_on is True

    def test_alt_screen_47l(self) -> None:
        self.parser.feed("\x1b[?47h")
        result = self.parser.feed("\x1b[?47l")
        assert result.alternate_on is False

    def test_alt_screen_overrides_state_detection(self) -> None:
        """While in alternate screen, state should be ALTERNATE_SCREEN regardless of content."""
        self.parser.feed("\x1b[?1049h")
        result = self.parser.feed("user@host$ ")
        assert result.state == TuiState.ALTERNATE_SCREEN


# ---------------------------------------------------------------------------
# OutputParser — cost extraction
# ---------------------------------------------------------------------------

class TestOutputParserCostExtraction:
    def setup_method(self) -> None:
        self.parser = OutputParser()

    def test_cost_with_k_tokens(self) -> None:
        result = self.parser.feed("Cost: $0.12 | Tokens: 1.2k in, 0.8k out")
        assert result.cost_dollars == 0.12
        assert result.tokens_in == 1200
        assert result.tokens_out == 800

    def test_cost_plain_tokens(self) -> None:
        result = self.parser.feed("$0.05 usage 500 in 300 out")
        assert result.cost_dollars == 0.05
        assert result.tokens_in == 500
        assert result.tokens_out == 300

    def test_no_cost(self) -> None:
        result = self.parser.feed("just some text without cost")
        assert result.cost_dollars is None
        assert result.tokens_in is None
        assert result.tokens_out is None


# ---------------------------------------------------------------------------
# OutputParser — tool extraction
# ---------------------------------------------------------------------------

class TestOutputParserToolExtraction:
    def setup_method(self) -> None:
        self.parser = OutputParser()

    def test_tool_colon(self) -> None:
        result = self.parser.feed("Tool: Read")
        assert result.tool_name == "Read"

    def test_using_tool(self) -> None:
        result = self.parser.feed("Using: Write")
        assert result.tool_name == "Write"

    def test_running_tool(self) -> None:
        result = self.parser.feed("Running: Bash")
        assert result.tool_name == "Bash"

    def test_no_tool(self) -> None:
        result = self.parser.feed("hello world")
        assert result.tool_name is None


# ---------------------------------------------------------------------------
# OutputParser — error extraction
# ---------------------------------------------------------------------------

class TestOutputParserErrorExtraction:
    def setup_method(self) -> None:
        self.parser = OutputParser()

    def test_error_keyword(self) -> None:
        result = self.parser.feed("Error: connection refused")
        assert result.error_text is not None
        assert "connection refused" in result.error_text

    def test_traceback(self) -> None:
        result = self.parser.feed("Traceback (most recent call last)")
        assert result.error_text is not None

    def test_fatal(self) -> None:
        result = self.parser.feed("FATAL: database is corrupted")
        assert result.error_text is not None

    def test_no_error(self) -> None:
        result = self.parser.feed("everything is fine")
        assert result.error_text is None


# ---------------------------------------------------------------------------
# OutputParser — bytes input
# ---------------------------------------------------------------------------

class TestOutputParserBytesInput:
    def setup_method(self) -> None:
        self.parser = OutputParser()

    def test_bytes_utf8(self) -> None:
        result = self.parser.feed(b"user@host$ ")
        assert result.state == TuiState.SHELL_PROMPT

    def test_bytes_with_invalid_utf8(self) -> None:
        """Should handle invalid UTF-8 gracefully without crashing."""
        result = self.parser.feed(b"\xff\xfe user@host$ ")
        # Replacement characters may interfere with prompt regex — just ensure no crash
        assert isinstance(result, ParseResult)


# ---------------------------------------------------------------------------
# OutputParser — stateful behavior
# ---------------------------------------------------------------------------

class TestOutputParserStateful:
    def setup_method(self) -> None:
        self.parser = OutputParser()

    def test_state_persists(self) -> None:
        """Last known state should persist when new chunk has no clear state."""
        self.parser.feed("⠋ Thinking...")
        result = self.parser.feed("some continuation text")
        assert result.state == TuiState.CLAUDE_WORKING

    def test_state_transitions(self) -> None:
        """Parser should detect transitions between states."""
        r1 = self.parser.feed("⠋ Working on it...")
        assert r1.state == TuiState.CLAUDE_WORKING

        r2 = self.parser.feed("Do you want to proceed?")
        assert r2.state == TuiState.CLAUDE_WAITING

    def test_reset(self) -> None:
        self.parser.feed("\x1b[?1049h")
        assert self.parser.alternate_on is True
        self.parser.reset()
        assert self.parser.alternate_on is False
        assert self.parser.last_state == TuiState.UNKNOWN

    def test_prompt_text_captured(self) -> None:
        result = self.parser.feed("user@host$ ")
        assert result.prompt_detected is True
        assert "user@host$" in result.prompt_text

    def test_ansi_in_prompt(self) -> None:
        """Shell prompt with ANSI color codes should still be detected."""
        result = self.parser.feed("\x1b[32muser@host\x1b[0m$ ")
        assert result.state == TuiState.SHELL_PROMPT


# ---------------------------------------------------------------------------
# ParseResult defaults
# ---------------------------------------------------------------------------

class TestParseResult:
    def test_defaults(self) -> None:
        r = ParseResult()
        assert r.state == TuiState.UNKNOWN
        assert r.prompt_detected is False
        assert r.prompt_text == ""
        assert r.alternate_on is False
        assert r.cost_dollars is None
        assert r.tokens_in is None
        assert r.tokens_out is None
        assert r.tool_name is None
        assert r.error_text is None

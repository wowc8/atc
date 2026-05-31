from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from atc.providers.claude_code.runtime import ClaudeCodeRuntime
from atc.runtime.models import ReadinessState, RoleKind, RuntimeSessionHandle, RuntimeTransport, StartRoleRequest


def test_claude_code_runtime_metadata() -> None:
    runtime = ClaudeCodeRuntime()
    assert runtime.provider_name == "claude_code"
    assert runtime.tmux_session == "atc"


def test_claude_code_runtime_prepare_workspace_creates_dir(tmp_path) -> None:
    runtime = ClaudeCodeRuntime()
    workdir = tmp_path / "repo"
    request = StartRoleRequest(
        session_id="sess-1",
        provider_name="claude_code",
        role=RoleKind.LEADER,
        working_dir=str(workdir),
    )

    asyncio.run(runtime.prepare_workspace(request))

    assert workdir.is_dir()


def test_claude_inspect_session_reports_stopped_when_pane_missing() -> None:
    runtime = ClaudeCodeRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-1",
        provider_name="claude_code",
        role=RoleKind.LEADER,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%1",
    )

    with patch("atc.providers.claude_code.runtime.pane_exists", AsyncMock(return_value=False)):
        inspection = asyncio.run(runtime.inspect_session(handle))

    assert inspection.alive is False
    assert inspection.readiness is ReadinessState.STOPPED


def test_claude_inspect_session_reports_ready_at_prompt() -> None:
    runtime = ClaudeCodeRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-2",
        provider_name="claude_code",
        role=RoleKind.LEADER,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%2",
    )

    with (
        patch("atc.providers.claude_code.runtime.pane_exists", AsyncMock(return_value=True)),
        patch("atc.providers.claude_code.runtime.capture_pane_text", AsyncMock(return_value="some output\n❯\n")),
    ):
        inspection = asyncio.run(runtime.inspect_session(handle))

    assert inspection.alive is True
    assert inspection.readiness is ReadinessState.READY
    assert inspection.summary == "Prompt ready"


def test_claude_inspect_session_reports_busy_when_not_at_prompt() -> None:
    runtime = ClaudeCodeRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-3",
        provider_name="claude_code",
        role=RoleKind.LEADER,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%3",
    )

    with (
        patch("atc.providers.claude_code.runtime.pane_exists", AsyncMock(return_value=True)),
        patch("atc.providers.claude_code.runtime.capture_pane_text", AsyncMock(return_value="Thinking hard...")),
    ):
        inspection = asyncio.run(runtime.inspect_session(handle))

    assert inspection.alive is True
    assert inspection.readiness is ReadinessState.BUSY



def test_claude_inspect_session_reports_blocked_on_trust() -> None:
    runtime = ClaudeCodeRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-4",
        provider_name="claude_code",
        role=RoleKind.LEADER,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%4",
    )

    with (
        patch("atc.providers.claude_code.runtime.pane_exists", AsyncMock(return_value=True)),
        patch("atc.providers.claude_code.runtime.capture_pane_text", AsyncMock(return_value="Do you trust this folder?")),
    ):
        inspection = asyncio.run(runtime.inspect_session(handle))

    assert inspection.readiness is ReadinessState.BLOCKED
    assert inspection.block_reason is not None
    assert inspection.summary == "Blocked on trust prompt"


def test_claude_inspect_session_reports_blocked_on_auth() -> None:
    runtime = ClaudeCodeRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-5",
        provider_name="claude_code",
        role=RoleKind.LEADER,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%5",
    )

    with (
        patch("atc.providers.claude_code.runtime.pane_exists", AsyncMock(return_value=True)),
        patch("atc.providers.claude_code.runtime.capture_pane_text", AsyncMock(return_value="Please login with your API key")),
    ):
        inspection = asyncio.run(runtime.inspect_session(handle))

    assert inspection.readiness is ReadinessState.BLOCKED
    assert inspection.summary == "Blocked on authentication"



def test_claude_restore_session_marks_ready_restore() -> None:
    runtime = ClaudeCodeRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-6",
        provider_name="claude_code",
        role=RoleKind.LEADER,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%6",
    )

    with (
        patch("atc.providers.claude_code.runtime.pane_exists", AsyncMock(return_value=True)),
        patch("atc.providers.claude_code.runtime.capture_pane_text", AsyncMock(return_value="output\n❯\n")),
    ):
        inspection = asyncio.run(runtime.restore_session(handle))

    assert inspection.summary == "Restored and ready"
    assert inspection.details["restore_attempted"] is True
    assert inspection.details["restore_usable"] is True


def test_claude_restore_session_marks_blocked_restore() -> None:
    runtime = ClaudeCodeRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-7",
        provider_name="claude_code",
        role=RoleKind.LEADER,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%7",
    )

    with (
        patch("atc.providers.claude_code.runtime.pane_exists", AsyncMock(return_value=True)),
        patch("atc.providers.claude_code.runtime.capture_pane_text", AsyncMock(return_value="Please login with your API key")),
    ):
        inspection = asyncio.run(runtime.restore_session(handle))

    assert inspection.summary == "Restore blocked: Blocked on authentication"
    assert inspection.details["restore_needs_attention"] is True



def test_claude_restore_session_marks_warming_up_stage() -> None:
    runtime = ClaudeCodeRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-8",
        provider_name="claude_code",
        role=RoleKind.LEADER,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%8",
    )

    with (
        patch("atc.providers.claude_code.runtime.pane_exists", AsyncMock(return_value=True)),
        patch("atc.providers.claude_code.runtime.capture_pane_text", AsyncMock(return_value="Welcome to Claude Code")),
    ):
        inspection = asyncio.run(runtime.restore_session(handle))

    assert inspection.details["provider_restore_stage"] == "warming_up"
    assert inspection.details["provider_restore_action"] == "wait"



def test_claude_inspect_session_exposes_runtime_hint() -> None:
    runtime = ClaudeCodeRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-9",
        provider_name="claude_code",
        role=RoleKind.LEADER,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%9",
    )

    with (
        patch("atc.providers.claude_code.runtime.pane_exists", AsyncMock(return_value=True)),
        patch("atc.providers.claude_code.runtime.capture_pane_text", AsyncMock(return_value="Welcome to Claude Code")),
    ):
        inspection = asyncio.run(runtime.inspect_session(handle))

    assert inspection.details["provider_runtime_hint"] == "startup_banner"
    assert inspection.details["provider_runtime_action"] == "wait"

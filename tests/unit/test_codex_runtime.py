from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from atc.providers.codex.runtime import CodexRuntime
from atc.runtime.models import (
    ReadinessState,
    RoleKind,
    RuntimeSessionHandle,
    RuntimeTransport,
    StartRoleRequest,
)


def test_codex_runtime_metadata() -> None:
    runtime = CodexRuntime()
    assert runtime.provider_name == "codex"
    assert runtime.tmux_session == "atc"


def test_codex_runtime_prepare_workspace_creates_dir(tmp_path) -> None:
    runtime = CodexRuntime()
    workdir = tmp_path / "repo"
    request = StartRoleRequest(
        session_id="sess-1",
        provider_name="codex",
        role=RoleKind.ACE,
        working_dir=str(workdir),
    )

    asyncio.run(runtime.prepare_workspace(request))

    assert workdir.is_dir()


def test_codex_inspect_session_reports_stopped_when_pane_missing() -> None:
    runtime = CodexRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-1",
        provider_name="codex",
        role=RoleKind.ACE,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%1",
    )

    with patch("atc.providers.codex.runtime.pane_exists", AsyncMock(return_value=False)):
        inspection = asyncio.run(runtime.inspect_session(handle))

    assert inspection.alive is False
    assert inspection.readiness is ReadinessState.STOPPED


def test_codex_inspect_session_reports_ready_at_prompt() -> None:
    runtime = CodexRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-2",
        provider_name="codex",
        role=RoleKind.ACE,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%2",
    )

    with (
        patch("atc.providers.codex.runtime.pane_exists", AsyncMock(return_value=True)),
        patch(
            "atc.providers.codex.runtime.capture_pane_text", AsyncMock(return_value="all good\n>\n")
        ),
    ):
        inspection = asyncio.run(runtime.inspect_session(handle))

    assert inspection.alive is True
    assert inspection.readiness is ReadinessState.READY
    assert inspection.summary == "Prompt ready"


def test_codex_inspect_session_reports_busy_when_not_at_prompt() -> None:
    runtime = CodexRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-3",
        provider_name="codex",
        role=RoleKind.ACE,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%3",
    )

    with (
        patch("atc.providers.codex.runtime.pane_exists", AsyncMock(return_value=True)),
        patch(
            "atc.providers.codex.runtime.capture_pane_text",
            AsyncMock(return_value="Processing request..."),
        ),
    ):
        inspection = asyncio.run(runtime.inspect_session(handle))

    assert inspection.alive is True
    assert inspection.readiness is ReadinessState.BUSY


def test_codex_inspect_session_reports_blocked_on_auth() -> None:
    runtime = CodexRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-4",
        provider_name="codex",
        role=RoleKind.ACE,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%4",
    )

    with (
        patch("atc.providers.codex.runtime.pane_exists", AsyncMock(return_value=True)),
        patch(
            "atc.providers.codex.runtime.capture_pane_text",
            AsyncMock(return_value="Sign in to continue"),
        ),
    ):
        inspection = asyncio.run(runtime.inspect_session(handle))

    assert inspection.readiness is ReadinessState.BLOCKED
    assert inspection.summary == "Blocked on authentication"


def test_codex_inspect_session_reports_blocked_on_permission_prompt() -> None:
    runtime = CodexRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-permission",
        provider_name="codex",
        role=RoleKind.ACE,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%permission",
    )

    with (
        patch("atc.providers.codex.runtime.pane_exists", AsyncMock(return_value=True)),
        patch(
            "atc.providers.codex.runtime.capture_pane_text",
            AsyncMock(return_value="Allow this command to continue?"),
        ),
    ):
        inspection = asyncio.run(runtime.inspect_session(handle))

    assert inspection.readiness is ReadinessState.BLOCKED
    assert inspection.summary == "Blocked on permission prompt"
    assert inspection.details["runtime_interrupt"] == "permission_prompt"
    assert inspection.details["provider_runtime_action"] == "resolve_permission"


def test_codex_restore_session_marks_ready_restore() -> None:
    runtime = CodexRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-5",
        provider_name="codex",
        role=RoleKind.ACE,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%5",
    )

    with (
        patch("atc.providers.codex.runtime.pane_exists", AsyncMock(return_value=True)),
        patch("atc.providers.codex.runtime.capture_pane_text", AsyncMock(return_value="ok\n>\n")),
    ):
        inspection = asyncio.run(runtime.restore_session(handle))

    assert inspection.summary == "Restored and ready"
    assert inspection.details["restore_attempted"] is True
    assert inspection.details["restore_usable"] is True


def test_codex_restore_session_marks_stopped_restore() -> None:
    runtime = CodexRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-6",
        provider_name="codex",
        role=RoleKind.ACE,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%6",
    )

    with patch("atc.providers.codex.runtime.pane_exists", AsyncMock(return_value=False)):
        inspection = asyncio.run(runtime.restore_session(handle))

    assert inspection.summary == "Restore failed: pane missing"
    assert inspection.details["restore_needs_attention"] is True


def test_codex_restore_session_marks_auth_gate_action() -> None:
    runtime = CodexRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-7",
        provider_name="codex",
        role=RoleKind.ACE,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%7",
    )

    with (
        patch("atc.providers.codex.runtime.pane_exists", AsyncMock(return_value=True)),
        patch(
            "atc.providers.codex.runtime.capture_pane_text",
            AsyncMock(return_value="Sign in to continue"),
        ),
    ):
        inspection = asyncio.run(runtime.restore_session(handle))

    assert inspection.details["provider_restore_stage"] == "auth_gate"
    assert inspection.details["provider_restore_action"] == "resolve_auth"


def test_codex_inspect_session_exposes_runtime_hint() -> None:
    runtime = CodexRuntime()
    handle = RuntimeSessionHandle(
        session_id="sess-8",
        provider_name="codex",
        role=RoleKind.ACE,
        transport=RuntimeTransport.TMUX,
        tmux_pane="%8",
    )

    with (
        patch("atc.providers.codex.runtime.pane_exists", AsyncMock(return_value=True)),
        patch(
            "atc.providers.codex.runtime.capture_pane_text",
            AsyncMock(return_value="Sign in to continue"),
        ),
    ):
        inspection = asyncio.run(runtime.inspect_session(handle))

    assert inspection.details["provider_runtime_hint"] == "auth_prompt"
    assert inspection.details["provider_runtime_action"] == "resolve_auth"

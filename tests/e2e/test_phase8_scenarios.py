"""Phase 8 scenario regression suite for runtime/orchestration guarantees.

These tests intentionally stitch together the known regression cluster from
``docs/runtime_orchestration_refactor_phases.md`` so future phases cannot satisfy
only isolated unit contracts while regressing the operator workflow.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from atc.api.app import create_app
from atc.config import Settings
from atc.providers.claude_code.runtime import ClaudeCodeRuntime
from atc.providers.codex.runtime import CodexRuntime
from atc.runtime.models import (
    ReadinessState,
    RoleKind,
    RuntimeDeliveryResult,
    RuntimeInspection,
    RuntimeSessionHandle,
    RuntimeTransport,
)
from atc.runtime.service import RuntimeService
from atc.runtime.tmux.runner import TmuxSessionRunner
from atc.runtime.tracing import (
    DeliveryAction,
    DeliveryReasonCode,
    DeliveryStage,
    DeliveryVerdict,
)
from atc.session.reconcile import reconcile_runtime_state
from atc.state import db as db_ops

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db_path = str(tmp_path / "phase8-api.db")
    settings = Settings(database={"path": db_path})  # type: ignore[arg-type]
    app = create_app(settings)
    with (
        patch("atc.leader.leader._accept_trust_dialog", new_callable=AsyncMock, return_value=False),
        patch("atc.tower.controller.TowerController.start_session", new_callable=AsyncMock),
        TestClient(app) as c,
    ):
        yield c


def _handle(
    session_id: str = "sess-phase8",
    *,
    provider: str = "codex",
    role: RoleKind = RoleKind.ACE,
    pane: str = "%phase8",
) -> RuntimeSessionHandle:
    return RuntimeSessionHandle(
        session_id=session_id,
        provider_name=provider,
        role=role,
        transport=RuntimeTransport.TMUX,
        tmux_session="atc",
        tmux_pane=pane,
    )


async def _create_project_session_task(
    db_path: str,
    *,
    session_status: str = "working",
    task_status: str = "in_progress",
):
    await db_ops.run_migrations(db_path)
    async with db_ops.get_connection(db_path) as conn:
        project = await db_ops.create_project(conn, "phase8-project", agent_provider="codex")
        ace = await db_ops.create_session(
            conn,
            project.id,
            "ace",
            "phase8-ace",
            provider="codex",
            status=session_status,
        )
        await db_ops.update_session_tmux(conn, ace.id, "atc", "%phase8")
        task = await db_ops.create_task_graph(
            conn,
            project.id,
            "phase8 task",
            status=task_status,
            assigned_ace_id=ace.id,
        )
        return project, await db_ops.get_session(conn, ace.id), task


def test_restart_restore_scenario_preserves_readiness_without_respawn() -> None:
    """Restart/restore must inspect existing runtime state and classify blockers."""

    codex = CodexRuntime()
    claude = ClaudeCodeRuntime()

    with (
        patch("atc.providers.codex.runtime.pane_exists", AsyncMock(return_value=True)),
        patch(
            "atc.providers.codex.runtime.capture_pane_text",
            AsyncMock(return_value="ready\n>\n"),
        ),
    ):
        ready = asyncio.run(codex.restore_session(_handle("codex-ready")))

    with (
        patch("atc.providers.claude_code.runtime.pane_exists", AsyncMock(return_value=True)),
        patch(
            "atc.providers.claude_code.runtime.capture_pane_text",
            AsyncMock(return_value="Please login with your API key"),
        ),
    ):
        blocked = asyncio.run(
            claude.restore_session(
                _handle("claude-blocked", provider="claude_code", role=RoleKind.LEADER)
            )
        )

    assert ready.alive is True
    assert ready.readiness is ReadinessState.READY
    assert ready.details["restore_usable"] is True
    assert blocked.readiness is ReadinessState.BLOCKED
    assert blocked.details["restore_needs_attention"] is True
    assert blocked.details["provider_restore_action"] == "resolve_auth"


def test_codex_stale_trust_scrollback_does_not_block_ready_prompt() -> None:
    """Resolved trust text in scrollback must not block delivery to a ready Codex prompt."""

    excerpt = """
Do you trust the contents of this directory?
› 1. Yes, continue
Press enter to continue

╭──────────────────────────────────────────────────────────╮
│ >_ OpenAI Codex (v0.137.0)                               │
│ model:       gpt-5.5   /model to change                  │
╰──────────────────────────────────────────────────────────╯

› Implement {feature}

  gpt-5.5 default · /private/tmp/atc-agents/session
"""
    codex = CodexRuntime()

    assert codex._detect_interrupt(excerpt) is None
    assert codex._classify_readiness(excerpt) == (ReadinessState.READY, None)


def test_dialog_interruption_scenario_blocks_before_delivery() -> None:

    """A visible trust dialog is a blocker, not a successful delivery."""

    metadata: dict[str, object] = {}
    runner = TmuxSessionRunner(
        tmux_session="atc",
        provider_name="claude_code",
        prompt_state_for_excerpt=lambda text: "ready" if "❯" in text else "busy",
        terminal_verdict_for_observation=lambda _text, _state, _after: pytest.fail(
            "terminal verdict must not run when trust dialog blocks before write"
        ),
        interrupt_detector=ClaudeCodeRuntime()._detect_interrupt,
    )

    with (
        patch("atc.runtime.tmux.runner.pane_exists", AsyncMock(return_value=True)),
        patch(
            "atc.runtime.tmux.runner.capture_pane_text",
            AsyncMock(return_value="Do you trust this folder?"),
        ),
        patch("atc.runtime.tmux.runner.send_bracketed_instruction", AsyncMock()) as send,
        pytest.raises(Exception, match="runtime interrupt"),
    ):
        asyncio.run(
            runner.deliver_instruction(
                handle=_handle(provider="claude_code", role=RoleKind.LEADER),
                metadata=metadata,
                trace_id="phase8-trust",
                action=DeliveryAction.INSTRUCTION,
                payload_loader=AsyncMock(return_value="do work"),
            )
        )

    send.assert_not_awaited()
    event = metadata["delivery_trace_events"][-1]  # type: ignore[index]
    assert event["stage"] == DeliveryStage.BLOCKED.value
    assert event["verdict"] == DeliveryVerdict.BLOCKED.value
    assert event["reason_code"] == DeliveryReasonCode.TRUST_REQUIRED.value


def test_permission_prompt_intercept_blocks_before_pty_write() -> None:
    """A visible permission prompt is a blocker, not pasted-behind-agent output."""

    metadata: dict[str, object] = {}
    runner = TmuxSessionRunner(
        tmux_session="atc",
        provider_name="codex",
        prompt_state_for_excerpt=CodexRuntime()._prompt_state_for_excerpt,
        terminal_verdict_for_observation=lambda _text, _state, _after: pytest.fail(
            "terminal verdict must not run when permission prompt blocks before write"
        ),
        interrupt_detector=CodexRuntime()._detect_interrupt,
    )

    with (
        patch("atc.runtime.tmux.runner.pane_exists", AsyncMock(return_value=True)),
        patch(
            "atc.runtime.tmux.runner.capture_pane_text",
            AsyncMock(return_value="Allow this command to continue?"),
        ),
        patch("atc.runtime.tmux.runner.send_bracketed_instruction", AsyncMock()) as send,
        pytest.raises(Exception, match="runtime interrupt"),
    ):
        asyncio.run(
                runner.deliver_instruction(
                    handle=_handle(),
                    metadata=metadata,
                    trace_id="phase8-permission",
                    action=DeliveryAction.INSTRUCTION,
                    payload_loader=AsyncMock(return_value="do work"),
                )
            )

    send.assert_not_awaited()
    event = metadata["delivery_trace_events"][-1]  # type: ignore[index]
    assert event["stage"] == DeliveryStage.BLOCKED.value
    assert event["reason_code"] == DeliveryReasonCode.PERMISSION_REQUIRED.value


@pytest.mark.asyncio
async def test_ace_retry_after_partial_failure_marks_error_then_reconcile_resets(
    tmp_path: Path,
) -> None:
    """Partial Ace delivery failure must be visible and recoverable by reconcile."""

    db_path = str(tmp_path / "phase8-partial-failure.db")
    _project, ace, task = await _create_project_session_task(db_path)
    assert ace is not None

    async with db_ops.get_connection(db_path) as conn:
        # Simulate a delivery path that marked the session erroneous after a partial
        # failure, then was retried by marking the task assigned again to a stale Ace.
        await db_ops.update_session_status(conn, ace.id, "error")
        failed = await db_ops.get_session(conn, ace.id)
        assert failed is not None
        assert failed.status == "error"

        await db_ops.update_task_graph_status(conn, task.id, "assigned")
        service = AsyncMock()
        service.inspect_session_record.return_value = RuntimeInspection(
            session_id=ace.id,
            provider_name="codex",
            alive=False,
            readiness=ReadinessState.STOPPED,
            summary="pane missing after partial failure",
        )

        result = await reconcile_runtime_state(conn, repair=True, runtime_service=service)

        task_after = await db_ops.get_task_graph(conn, task.id)
        session_after = await db_ops.get_session(conn, ace.id)

    assert result.summary["orphaned_task"] >= 1
    assert task_after is not None
    assert task_after.status == "todo"
    assert task_after.assigned_ace_id is None
    assert session_after is not None
    assert session_after.status == "error"


@pytest.mark.asyncio
async def test_stale_active_session_recovery_quarantines_unknown_runtime(
    tmp_path: Path,
) -> None:
    """Stale active-session recovery repairs only proven stale panes."""

    db_path = str(tmp_path / "phase8-stale-recovery.db")
    await _create_project_session_task(db_path, session_status="working")

    async with db_ops.get_connection(db_path) as conn:
        service = AsyncMock()
        service.inspect_session_record.return_value = RuntimeInspection(
            session_id="phase8",
            provider_name="codex",
            alive=False,
            readiness=ReadinessState.STOPPED,
            summary="pane missing",
        )

        result = await reconcile_runtime_state(conn, repair=True, runtime_service=service)
        finding = next(item for item in result.findings if item.kind == "stale_active_session")
        repaired = await db_ops.get_session(conn, finding.session_id or finding.entity_id)

    assert finding.reason_code == "runtime_not_alive"
    assert finding.repair_status == "applied"
    assert repaired is not None
    assert repaired.status == "disconnected"
    assert repaired.tmux_pane is None


def test_retry_path_task_transitions_are_explicit(client: TestClient) -> None:
    """Retry paths must bridge through allowed task states, not jump silently."""

    project = client.post(
        "/api/projects",
        json={"name": "phase8 retry path", "agent_provider": "codex"},
    ).json()
    task = client.post(
        f"/api/projects/{project['id']}/task-graphs",
        json={"title": "retry me", "status": "todo"},
    ).json()
    start = client.post(
        f"/api/projects/{project['id']}/leader/start",
        json={"goal": "Phase 8 retry path", "auto_kickoff": False},
    )
    assert start.status_code == 200
    spawn = client.post(f"/api/projects/{project['id']}/leader/spawn-aces", json={})
    assert spawn.status_code == 200
    spawned = spawn.json()["spawned"]
    assert spawned
    assert spawned[0]["task_graph_id"] == task["id"]

    after_spawn = client.get(f"/api/task-graphs/{task['id']}")
    assert after_spawn.status_code == 200
    assert after_spawn.json()["status"] == "in_progress"
    assert after_spawn.json()["assigned_ace_id"] == spawned[0]["ace_session_id"]

    errored = client.patch(
        f"/api/task-graphs/{task['id']}/status",
        json={"status": "error"},
    )
    assert errored.status_code == 200
    retried_status = client.patch(
        f"/api/task-graphs/{task['id']}/status",
        json={"status": "todo"},
    )
    assert retried_status.status_code == 200
    retried = client.patch(
        f"/api/task-graphs/{task['id']}",
        json={"assigned_ace_id": None},
    )
    assert retried.status_code == 200
    assert retried.json()["status"] == "todo"
    assert retried.json()["assigned_ace_id"] is None


def test_tower_driven_project_flow_manages_leader_and_ace(client: TestClient) -> None:
    """Tower-driven project flow must create Leader/Ace state with truthful delivery."""

    project = client.post(
        "/api/projects",
        json={"name": "phase8 tower flow", "agent_provider": "codex"},
    ).json()
    task = client.post(
        f"/api/projects/{project['id']}/task-graphs",
        json={"title": "phase8 tower-managed ace task"},
    ).json()

    with (
        patch(
            "atc.tracking.resources.ResourceGovernor.get_system_usage",
            return_value=(0.0, 0.0),
        ),
        patch.object(
            RuntimeService,
            "send_instruction",
            new_callable=AsyncMock,
            return_value=RuntimeDeliveryResult(
                session_id="ace-phase8",
                provider_name="codex",
                role=RoleKind.ACE,
                status="confirmed",
                stage="agent_output_observed",
                verdict="confirmed",
                reason_code="agent_output",
                trace_id="phase8-confirmed",
            ),
        ),
    ):
        start = client.post(
            f"/api/projects/{project['id']}/leader/start",
            json={"goal": "Phase 8 scenario", "auto_kickoff": False},
        )
        assert start.status_code == 200
        assert start.json()["delivery_state"] == "started"

        spawn = client.post(f"/api/projects/{project['id']}/leader/spawn-aces", json={})
        assert spawn.status_code == 200
        spawned = spawn.json()["spawned"]
        assert spawned

        instruct = client.post(
            f"/api/projects/{project['id']}/leader/instruct",
            json={
                "task_graph_id": task["id"],
                "instruction": "Phase 8 scenario assignment",
            },
        )
        assert instruct.status_code == 200
        assert instruct.json()["delivery_state"] == "confirmed"

    refreshed = client.get(f"/api/task-graphs/{task['id']}")
    assert refreshed.status_code == 200
    assert refreshed.json()["assigned_ace_id"] == spawned[0]["ace_session_id"]
    assert refreshed.json()["status"] in {"assigned", "in_progress"}


def test_blocked_ace_instruction_preserves_assignment_for_operator_resolution(
    client: TestClient,
) -> None:
    """Blocked runtime prompts must not fail/destroy the Ace before operator action."""

    project = client.post(
        "/api/projects",
        json={"name": "phase8 blocked ace", "agent_provider": "codex"},
    ).json()
    task = client.post(
        f"/api/projects/{project['id']}/task-graphs",
        json={"title": "blocked trust prompt task"},
    ).json()

    with (
        patch(
            "atc.tracking.resources.ResourceGovernor.get_system_usage",
            return_value=(0.0, 0.0),
        ),
        patch.object(
            RuntimeService,
            "send_instruction",
            new_callable=AsyncMock,
            return_value=RuntimeDeliveryResult(
                session_id="ace-phase8-blocked",
                provider_name="codex",
                role=RoleKind.ACE,
                status="blocked",
                stage="blocked",
                verdict="blocked",
                reason_code="trust_required",
                trace_id="phase8-blocked",
            ),
        ),
    ):
        start = client.post(
            f"/api/projects/{project['id']}/leader/start",
            json={"goal": "Phase 8 blocked scenario", "auto_kickoff": False},
        )
        assert start.status_code == 200

        spawn = client.post(f"/api/projects/{project['id']}/leader/spawn-aces", json={})
        assert spawn.status_code == 200
        spawned = spawn.json()["spawned"]
        assert spawned
        ace_session_id = spawned[0]["ace_session_id"]

        instruct = client.post(
            f"/api/projects/{project['id']}/leader/instruct",
            json={
                "task_graph_id": task["id"],
                "instruction": "This should block on trust, not fail the Ace.",
            },
        )
        assert instruct.status_code == 200
        assert instruct.json()["delivery_state"] == "blocked"

    refreshed = client.get(f"/api/task-graphs/{task['id']}")
    assert refreshed.status_code == 200
    assert refreshed.json()["assigned_ace_id"] == ace_session_id
    assert refreshed.json()["status"] == "in_progress"

    aces = client.get(f"/api/projects/{project['id']}/aces")
    assert aces.status_code == 200
    ace = next(item for item in aces.json() if item["id"] == ace_session_id)
    assert ace["status"] == "waiting"

    progress = client.get(f"/api/projects/{project['id']}/leader/progress")
    assert progress.status_code == 200
    assignments = progress.json()["assignments"]
    assert assignments[0]["task_graph_id"] == task["id"]
    assert assignments[0]["status"] == "assigned"

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path
from fastapi.testclient import TestClient

from atc.api.app import create_app
from atc.config import Settings
from atc.runtime.models import ReadinessState, RuntimeInspection
from atc.session.reconcile import (
    ReconcileFinding,
    _mark_session_stale,
    _reset_orphaned_task,
    reconcile_runtime_state,
)
from atc.state import db as db_ops


async def _make_project_and_session(conn, *, status: str = "working"):
    project = await db_ops.create_project(conn, "phase6-project", agent_provider="codex")
    session = await db_ops.create_session(
        conn,
        project.id,
        "ace",
        "ace-phase6",
        provider="codex",
        status=status,
    )
    await db_ops.update_session_tmux(conn, session.id, "atc", "%missing")
    return project, await db_ops.get_session(conn, session.id)


@pytest.mark.asyncio
async def test_reconcile_marks_stale_active_session_disconnected(tmp_path: Path) -> None:
    db_path = str(tmp_path / "phase6.db")
    await db_ops.run_migrations(db_path)
    async with db_ops.get_connection(db_path) as conn:
        _project, session = await _make_project_and_session(conn)
        assert session is not None
        service = AsyncMock()
        service.inspect_session_record.return_value = RuntimeInspection(
            session_id=session.id,
            provider_name="codex",
            alive=False,
            readiness=ReadinessState.STOPPED,
            summary="pane missing",
        )

        result = await reconcile_runtime_state(conn, repair=True, runtime_service=service)

        assert result.summary["stale_active_session"] == 1
        finding = result.findings[0]
        assert finding.reason_code == "runtime_not_alive"
        assert finding.repair_status == "applied"
        repaired = await db_ops.get_session(conn, session.id)
        assert repaired is not None
        assert repaired.status == "disconnected"
        assert repaired.tmux_pane is None
        events = await db_ops.list_app_events(conn, session_id=session.id)
        assert events
        assert events[0].category == "reconcile"


@pytest.mark.asyncio
async def test_reconcile_resets_orphaned_task_for_reassignment(tmp_path: Path) -> None:
    db_path = str(tmp_path / "phase6-orphan.db")
    await db_ops.run_migrations(db_path)
    async with db_ops.get_connection(db_path) as conn:
        project = await db_ops.create_project(conn, "phase6-orphan", agent_provider="codex")
        task = await db_ops.create_task_graph(
            conn,
            project.id,
            "orphaned task",
            status="assigned",
            assigned_ace_id="missing-ace-session",
        )

        result = await reconcile_runtime_state(conn, repair=True, runtime_service=AsyncMock())

        assert result.summary["orphaned_task"] == 1
        finding = result.findings[0]
        assert finding.reason_code == "assigned_ace_not_live"
        assert finding.repair_status == "applied"
        repaired = await db_ops.get_task_graph(conn, task.id)
        assert repaired is not None
        assert repaired.status == "todo"
        assert repaired.assigned_ace_id is None
        events = await db_ops.list_app_events(conn, session_id="missing-ace-session")
        assert events
        assert events[0].category == "reconcile"


def test_reconcile_api_reports_orphaned_task(tmp_path: Path) -> None:
    db_path = str(tmp_path / "phase6-api.db")
    settings = Settings(database={"path": db_path})  # type: ignore[arg-type]
    app = create_app(settings)
    with (
        patch("atc.leader.leader._accept_trust_dialog", new_callable=AsyncMock, return_value=False),
        patch("atc.tower.controller.TowerController.start_session", new_callable=AsyncMock),
        TestClient(app) as client,
    ):
        project = client.post("/api/projects", json={"name": "phase6-api"}).json()
        task = client.post(
            f"/api/projects/{project['id']}/task-graphs",
            json={
                "title": "api orphan",
                "status": "assigned",
                "assigned_ace_id": "api-missing-ace",
            },
        ).json()

        dry = client.post("/api/orchestration/reconcile", json={"repair": False})
        assert dry.status_code == 200
        data = dry.json()
        assert data["summary"]["orphaned_task"] == 1
        assert data["findings"][0]["repair_status"] == "not_requested"

        repaired = client.post("/api/orchestration/reconcile", json={"repair": True})
        assert repaired.status_code == 200
        assert repaired.json()["findings"][0]["repair_status"] == "applied"
        task_after = client.get(f"/api/task-graphs/{task['id']}").json()
        assert task_after["status"] == "todo"
        assert task_after["assigned_ace_id"] is None


@pytest.mark.asyncio
async def test_reconcile_inspection_failure_is_not_auto_repaired(tmp_path: Path) -> None:
    db_path = str(tmp_path / "phase6-inspection-failure.db")
    await db_ops.run_migrations(db_path)
    async with db_ops.get_connection(db_path) as conn:
        _project, session = await _make_project_and_session(conn)
        assert session is not None
        service = AsyncMock()
        service.inspect_session_record.side_effect = RuntimeError("tmux unavailable")

        result = await reconcile_runtime_state(conn, repair=True, runtime_service=service)

        finding = result.findings[0]
        assert finding.reason_code == "runtime_inspection_failed"
        assert finding.recommended_action == "require_operator_intervention"
        assert finding.repair_status == "skipped"
        unchanged = await db_ops.get_session(conn, session.id)
        assert unchanged is not None
        assert unchanged.status == "working"
        assert unchanged.tmux_pane == "%missing"


@pytest.mark.asyncio
async def test_reconcile_blocked_session_is_reported_not_repaired(tmp_path: Path) -> None:
    db_path = str(tmp_path / "phase6-blocked.db")
    await db_ops.run_migrations(db_path)
    async with db_ops.get_connection(db_path) as conn:
        _project, session = await _make_project_and_session(conn)
        assert session is not None
        service = AsyncMock()
        service.inspect_session_record.return_value = RuntimeInspection(
            session_id=session.id,
            provider_name="codex",
            alive=True,
            readiness=ReadinessState.BLOCKED,
            summary="Blocked on trust prompt",
        )

        result = await reconcile_runtime_state(conn, repair=True, runtime_service=service)

        finding = result.findings[0]
        assert finding.kind == "runtime_blocked"
        assert finding.repair_status == "skipped"
        unchanged = await db_ops.get_session(conn, session.id)
        assert unchanged is not None
        assert unchanged.status == "working"
        assert unchanged.tmux_pane == "%missing"


@pytest.mark.asyncio
async def test_reconcile_task_assignee_must_be_live_ace(tmp_path: Path) -> None:
    db_path = str(tmp_path / "phase6-live-non-ace.db")
    await db_ops.run_migrations(db_path)
    async with db_ops.get_connection(db_path) as conn:
        project = await db_ops.create_project(conn, "phase6-non-ace", agent_provider="codex")
        leader = await db_ops.create_session(
            conn,
            project.id,
            "leader",
            "leader-phase6",
            provider="codex",
            status="idle",
        )
        await db_ops.update_session_tmux(conn, leader.id, "atc", "%leader")
        task = await db_ops.create_task_graph(
            conn,
            project.id,
            "assigned to leader",
            status="assigned",
            assigned_ace_id=leader.id,
        )
        service = AsyncMock()
        service.inspect_session_record.return_value = RuntimeInspection(
            session_id=leader.id,
            provider_name="codex",
            alive=True,
            readiness=ReadinessState.READY,
        )

        result = await reconcile_runtime_state(conn, repair=True, runtime_service=service)

        finding = next(item for item in result.findings if item.task_graph_id == task.id)
        assert finding.kind == "orphaned_task"
        assert finding.repair_status == "applied"
        repaired = await db_ops.get_task_graph(conn, task.id)
        assert repaired is not None
        assert repaired.status == "todo"
        assert repaired.assigned_ace_id is None


@pytest.mark.asyncio
async def test_stale_session_repair_skips_if_session_changed(tmp_path: Path) -> None:
    db_path = str(tmp_path / "phase6-session-guard.db")
    await db_ops.run_migrations(db_path)
    async with db_ops.get_connection(db_path) as conn:
        _project, session = await _make_project_and_session(conn)
        assert session is not None
        await db_ops.update_session_tmux(conn, session.id, "atc", "%new-pane")
        finding = ReconcileFinding(
            kind="stale_active_session",
            severity="warning",
            entity_type="session",
            entity_id=session.id,
            reason_code="runtime_not_alive",
            message="stale",
            recommended_action="mark_stale",
            session_id=session.id,
            details={"status": "working", "tmux_pane": "%missing"},
        )

        await _mark_session_stale(conn, finding)

        assert finding.repair_status == "skipped"
        unchanged = await db_ops.get_session(conn, session.id)
        assert unchanged is not None
        assert unchanged.status == "working"
        assert unchanged.tmux_pane == "%new-pane"


@pytest.mark.asyncio
async def test_orphaned_task_repair_skips_if_assignment_changed(tmp_path: Path) -> None:
    db_path = str(tmp_path / "phase6-task-guard.db")
    await db_ops.run_migrations(db_path)
    async with db_ops.get_connection(db_path) as conn:
        project = await db_ops.create_project(conn, "phase6-task-guard", agent_provider="codex")
        task = await db_ops.create_task_graph(
            conn,
            project.id,
            "guarded task",
            status="assigned",
            assigned_ace_id="old-ace",
        )
        await db_ops.update_task_graph(conn, task.id, assigned_ace_id="new-ace")
        finding = ReconcileFinding(
            kind="orphaned_task",
            severity="warning",
            entity_type="task_graph",
            entity_id=task.id,
            reason_code="assigned_ace_not_live",
            message="orphaned",
            recommended_action="reset_task_for_reassignment",
            task_graph_id=task.id,
            session_id="old-ace",
            details={"assigned_ace_id": "old-ace", "task_status": "assigned"},
        )

        await _reset_orphaned_task(conn, finding)

        assert finding.repair_status == "skipped"
        unchanged = await db_ops.get_task_graph(conn, task.id)
        assert unchanged is not None
        assert unchanged.status == "assigned"
        assert unchanged.assigned_ace_id == "new-ace"

"""Tests for provider-neutral runtime health and recovery planning."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from atc.api.routers.aces import RecoveryRequest as AceRecoveryRequest
from atc.api.routers.aces import get_ace_health, recover_ace
from atc.api.routers.projects import RecoveryRequest as LeaderRecoveryRequest
from atc.api.routers.projects import get_leader_health, recover_leader
from atc.runtime.health import ace_health, build_recovery_plan, leader_health
from atc.runtime.models import ReadinessState, RuntimeBlockReason, RuntimeInspection
from atc.state.db import (
    _SCHEMA_SQL,
    assign_task,
    create_leader,
    create_project,
    create_session,
    create_task_graph,
    get_connection,
    run_migrations,
    update_task_assignment_dispatch,
)


@pytest.fixture
async def db():
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


class FakeRuntimeService:
    def __init__(self, inspection: RuntimeInspection | Exception) -> None:
        self.inspection = inspection

    async def inspect_session_record(self, _session):
        if isinstance(self.inspection, Exception):
            raise self.inspection
        return self.inspection


def _request(db):
    request = MagicMock()
    request.app.state.db = db
    return request


@pytest.mark.asyncio
async def test_leader_health_reports_runtime_and_task_summary(db) -> None:
    project = await create_project(db, "health-proj")
    leader = await create_leader(db, project.id, goal="Ship health")
    session = await create_session(
        db,
        project.id,
        "manager",
        "leader-health",
        status="working",
        provider="codex",
    )
    await db.execute(
        "UPDATE sessions SET tmux_pane = ?, tmux_session = ? WHERE id = ?",
        ("%7", "atc", session.id),
    )
    await db.execute(
        "UPDATE leaders SET session_id = ?, context = ? WHERE id = ?",
        (
            session.id,
            json.dumps(
                {"leader_kickoff_payload": {"message": "go"}, "leader_original_goal": "Ship health"}
            ),
            leader.id,
        ),
    )
    task = await create_task_graph(db, project.id, "Task one")
    ace = await create_session(db, project.id, "ace", "ace-one", status="working", task_id=task.id)
    assignment, _ = await assign_task(db, task.id, ace.id, f"{leader.id}:{task.id}")
    await update_task_assignment_dispatch(
        db,
        assignment.assignment_id,
        dispatch_delivery_state="accepted_active",
        dispatch_verified=True,
        last_activity=True,
    )
    await db.commit()

    health = await leader_health(
        db,
        project.id,
        runtime_service=FakeRuntimeService(
            RuntimeInspection(
                session_id=session.id,
                provider_name="codex",
                alive=True,
                readiness=ReadinessState.BUSY,
                summary="working",
            )
        ),
    )

    data = health.as_dict()
    assert data["runtime_state"] == "active"
    assert data["kickoff_state"]["kickoff_payload_persisted"] is True
    assert data["task_graph_state"]["total"] == 1
    assert data["ace_dispatch"]["verified"] == 1
    assert data["ace_count"] == 1


@pytest.mark.asyncio
async def test_ace_health_reports_blocked_dispatch_separately(db) -> None:
    project = await create_project(db, "ace-health-proj")
    task = await create_task_graph(db, project.id, "Task one")
    ace = await create_session(db, project.id, "ace", "ace-one", status="waiting", task_id=task.id)
    await db.execute(
        "UPDATE sessions SET tmux_pane = ?, tmux_session = ? WHERE id = ?",
        ("%9", "atc", ace.id),
    )
    assignment, _ = await assign_task(db, task.id, ace.id, f"ace:{task.id}")
    await update_task_assignment_dispatch(
        db,
        assignment.assignment_id,
        dispatch_delivery_state="blocked",
        dispatch_verified=False,
        blocker_reason="default_prompt_visible",
    )

    health = await ace_health(
        db,
        project.id,
        ace.id,
        runtime_service=FakeRuntimeService(
            RuntimeInspection(
                session_id=ace.id,
                provider_name="codex",
                alive=True,
                readiness=ReadinessState.BLOCKED,
                block_reason=RuntimeBlockReason.PROVIDER_PROMPT,
                summary="default prompt",
            )
        ),
    )

    data = health.as_dict()
    assert data["runtime_state"] == "blocked"
    assert data["ace_dispatch"]["dispatch_delivery_state"] == "blocked"
    assert data["ace_dispatch"]["dispatch_verified"] is False
    assert data["current_blocker"] == "unknown_prompt_blocker"


@pytest.mark.asyncio
async def test_recovery_dry_run_and_apply_refusal(db) -> None:
    project = await create_project(db, "recover-proj")
    await create_leader(db, project.id, goal="Recover")
    health = await leader_health(
        db, project.id, runtime_service=FakeRuntimeService(RuntimeError("boom"))
    )

    dry_run = build_recovery_plan(health, mode="dry_run")
    assert dry_run.mode == "dry_run"
    assert dry_run.actions[0]["action"] == "inspect_runtime"

    apply = build_recovery_plan(health, mode="apply")
    assert apply.refused_reason == "apply_requires_explicit_policy"

    explicit_apply = build_recovery_plan(health, mode="apply", policy="restart_missing_pane")
    assert explicit_apply.safe_to_apply is True
    assert explicit_apply.refused_reason is None


@pytest.mark.asyncio
async def test_ace_health_does_not_leak_cross_project_assignment(db) -> None:
    owner = await create_project(db, "owner-proj")
    other = await create_project(db, "other-proj")
    task = await create_task_graph(db, owner.id, "Secret task")
    ace = await create_session(db, owner.id, "ace", "ace-one", status="waiting", task_id=task.id)
    assignment, _ = await assign_task(db, task.id, ace.id, f"ace:{task.id}")
    await update_task_assignment_dispatch(
        db,
        assignment.assignment_id,
        dispatch_delivery_state="blocked",
        dispatch_verified=False,
        blocker_reason="secret_blocker",
    )

    health = await ace_health(db, other.id, ace.id)
    data = health.as_dict()
    assert data["runtime_exists"] is False
    assert data["ace_dispatch"] == {}
    assert data["task_graph_state"] == {}


@pytest.mark.asyncio
async def test_ace_health_api_404s_for_cross_project_session(db) -> None:
    owner = await create_project(db, "owner-api-proj")
    other = await create_project(db, "other-api-proj")
    ace = await create_session(db, owner.id, "ace", "ace-one", status="idle")
    request = _request(db)

    with pytest.raises(HTTPException) as exc_info:
        await get_ace_health(other.id, ace.id, request)
    assert exc_info.value.status_code == 404

    with pytest.raises(HTTPException) as recover_exc:
        await recover_ace(other.id, ace.id, AceRecoveryRequest(dry_run=True), request)
    assert recover_exc.value.status_code == 404


@pytest.mark.asyncio
async def test_health_and_recover_api_endpoints(db) -> None:
    project = await create_project(db, "api-health-proj")
    await create_leader(db, project.id, goal="API health")
    request = _request(db)

    leader = await get_leader_health(project.id, request)
    assert leader["role"] == "leader"
    assert leader["runtime_state"] == "missing"

    plan = await recover_leader(project.id, LeaderRecoveryRequest(dry_run=True), request)
    assert plan["mode"] == "dry_run"

    ace = await create_session(db, project.id, "ace", "ace-one", status="idle")
    ace_data = await get_ace_health(project.id, ace.id, request)
    assert ace_data["role"] == "ace"

    with pytest.raises(HTTPException) as exc_info:
        await recover_ace(project.id, ace.id, AceRecoveryRequest(dry_run=False), request)
    assert exc_info.value.status_code == 409

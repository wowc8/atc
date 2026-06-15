"""Tests for Leader kickoff payload persistence and verification."""

from __future__ import annotations

import json

import pytest

from atc.leader.kickoff import (
    build_leader_kickoff_message,
    persist_leader_kickoff_payload,
    report_leader_goal_accepted,
    verify_leader_kickoff_delivery,
)
from atc.runtime.models import (
    DeliveryState,
    RoleKind,
    RuntimeDeliveryResult,
    RuntimeState,
)
from atc.state.db import (
    _SCHEMA_SQL,
    create_leader,
    create_project,
    get_connection,
    run_migrations,
)


@pytest.fixture
async def db():
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.mark.asyncio
async def test_persist_leader_kickoff_payload_is_recoverable(db) -> None:
    project = await create_project(db, "kickoff-proj")
    await create_leader(db, project.id, goal="old goal")
    message = build_leader_kickoff_message(
        project_id=project.id,
        project_name=project.name,
        goal="Build runtime truth",
        description="phase 3",
        api_style="explicit-api",
    )

    payload = await persist_leader_kickoff_payload(
        db,
        project_id=project.id,
        goal="Build runtime truth",
        message=message,
        source="test",
    )

    cursor = await db.execute(
        "SELECT context, goal FROM leaders WHERE project_id = ?", (project.id,)
    )
    context_json, goal = await cursor.fetchone()
    context = json.loads(context_json)
    assert goal == "Build runtime truth"
    assert payload.goal == "Build runtime truth"
    assert context["leader_original_goal"] == "Build runtime truth"
    assert context["leader_kickoff_payload"]["message"] == message
    assert context["leader_kickoff_payload"]["source"] == "test"
    assert context["leader_kickoff_payload"]["trace_id"]
    assert payload.trace_id == context["leader_kickoff_payload"]["trace_id"]
    assert f"/api/projects/{project.id}/leader/report-active" in message
    assert f"/api/projects/{project.id}/leader/decompose" in message
    assert "/api/projects/{project_id}" not in message


def test_verify_leader_kickoff_accepts_active_delivery() -> None:
    result = RuntimeDeliveryResult(
        session_id="leader-1",
        provider_name="codex",
        role=RoleKind.LEADER,
        status="confirmed",
        stage="agent_output_observed",
        verdict="confirmed",
        reason_code="agent_output",
        runtime_state=RuntimeState.ACTIVE,
        delivery_state=DeliveryState.ACCEPTED_ACTIVE,
        trace_id="trace-active",
        last_activity_at="2026-06-13T00:00:00+00:00",
    )

    verification = verify_leader_kickoff_delivery(
        result,
        leader_reported_active=True,
        goal_accepted=True,
        first_actionable_step_observed_at="2026-06-13T00:00:30+00:00",
        task_graph_created_at="2026-06-13T00:00:30+00:00",
    )

    assert verification.kickoff_verified is True
    assert verification.kickoff_state == "leader_reported_active"
    assert verification.startup_handshake_state == "ready"
    assert verification.goal_acceptance_state == "leader_reported_active"
    assert verification.delivery_trace_id == "trace-active"
    assert verification.first_actionable_step_observed_at == "2026-06-13T00:00:30+00:00"
    assert verification.runtime_created is True
    assert verification.payload_written is True
    assert verification.submit_sent is True
    assert verification.provider_accepted is True
    assert verification.goal_accepted is True
    assert verification.leader_reported_active is True
    assert verification.leader_began_work is True


def test_verify_leader_kickoff_distinguishes_pending_submission() -> None:
    result = RuntimeDeliveryResult(
        session_id="leader-1",
        provider_name="codex",
        role=RoleKind.LEADER,
        status="accepted",
        stage="submit_attempted",
        verdict="accepted",
        reason_code="submit_sent",
        runtime_state=RuntimeState.READY,
        delivery_state=DeliveryState.SUBMIT_SENT,
    )

    verification = verify_leader_kickoff_delivery(result)

    assert verification.kickoff_verified is False
    assert verification.kickoff_state == "submitted_pending_acceptance"
    assert verification.startup_handshake_state == "ready"
    assert verification.goal_acceptance_state == "submitted_pending_acceptance"
    assert verification.payload_written is True
    assert verification.submit_sent is True
    assert verification.provider_accepted is False
    assert verification.goal_accepted is False
    assert verification.leader_reported_active is False
    assert verification.leader_began_work is False


@pytest.mark.asyncio
async def test_report_leader_goal_accepted_persists_active_report(db) -> None:
    project = await create_project(db, "leader-active-report")
    await create_leader(db, project.id, goal="Ship it")

    report = await report_leader_goal_accepted(
        db,
        project_id=project.id,
        goal_accepted=True,
        message="accepted",
    )

    cursor = await db.execute("SELECT context FROM leaders WHERE project_id = ?", (project.id,))
    (context_json,) = await cursor.fetchone()
    context = json.loads(context_json)
    assert report["leader_reported_active"] is True
    assert report["goal_accepted"] is True
    assert context["project_id"] == project.id
    assert context["goal"] == "Ship it"
    assert context["leader_active_report"]["leader_reported_active"] is True
    assert context["leader_active_report"]["goal_accepted"] is True
    assert context["leader_active_report"]["message"] == "accepted"


def test_verify_leader_kickoff_reports_blocker() -> None:
    from atc.runtime.models import BlockerReason

    result = RuntimeDeliveryResult(
        session_id="leader-1",
        provider_name="codex",
        role=RoleKind.LEADER,
        status="blocked",
        stage="blocked",
        verdict="blocked",
        reason_code="runtime_update_required",
        runtime_state=RuntimeState.BLOCKED,
        delivery_state=DeliveryState.BLOCKED,
        blocker_reason=BlockerReason.RUNTIME_UPDATE_REQUIRED,
    )

    verification = verify_leader_kickoff_delivery(result)

    assert verification.kickoff_verified is False
    assert verification.kickoff_state == "blocked"
    assert verification.startup_handshake_state == "blocked"
    assert verification.goal_acceptance_state == "blocked"
    assert verification.blocker_reason == "runtime_update_required"
    assert verification.kickoff_blocker_reason == "runtime_update_required"
    assert verification.kickoff_recovery_recommendation is not None

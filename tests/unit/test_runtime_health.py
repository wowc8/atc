"""Tests for provider-neutral runtime health and recovery planning."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from atc.api.routers.aces import (
    AceActiveReportRequest,
    get_ace_health,
    recover_ace,
    report_ace_active,
)
from atc.api.routers.aces import RecoveryRequest as AceRecoveryRequest
from atc.api.routers.projects import RecoveryRequest as LeaderRecoveryRequest
from atc.api.routers.projects import get_leader_health, recover_leader
from atc.runtime.health import (
    RuntimeHealth,
    ace_health,
    apply_recovery_plan,
    build_recovery_plan,
    leader_health,
)
from atc.runtime.models import (
    BlockerReason,
    ReadinessState,
    RuntimeBlockReason,
    RuntimeInspection,
    RuntimeState,
)
from atc.state.db import (
    _SCHEMA_SQL,
    assign_task,
    create_leader,
    create_project,
    create_session,
    create_task_graph,
    get_connection,
    get_session,
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
        self.submitted = False

    async def inspect_session_record(self, _session):
        if isinstance(self.inspection, Exception):
            raise self.inspection
        return self.inspection

    async def submit_pending_prompt_for_session_record(self, _session, _inspection):
        self.submitted = True
        return True


class FakeEventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))


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
                {
                    "leader_kickoff_payload": {"message": "go"},
                    "leader_original_goal": "Ship health",
                    "leader_active_report": {
                        "leader_reported_active": True,
                        "goal_accepted": True,
                        "reported_at": "2026-06-12T12:00:30+00:00",
                    },
                }
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
    assert data["kickoff_state"]["leader_reported_active"] is True
    assert data["kickoff_state"]["goal_accepted"] is True
    assert data["kickoff_state"]["kickoff_verified"] is True
    assert data["kickoff_state"]["kickoff_state"] == "working"
    assert data["leader_state"] == "working"
    assert data["recommended_command"] == (
        f"atc leader health --project-id {project.id} --summary"
    )
    assert data["task_graph_state"]["total"] == 1
    assert data["ace_dispatch"]["verified"] == 1
    assert data["ace_count"] == 1
    assert data["operator_guidance"]["severity"] == "ok"
    assert data["operator_guidance"]["recommended_action"] == "none"


@pytest.mark.asyncio
async def test_leader_health_treats_startup_trust_as_expected_pre_nudge_branch(db) -> None:
    project = await create_project(db, "leader-trust-startup")
    leader = await create_leader(db, project.id, goal="Handle trust")
    session = await create_session(
        db,
        project.id,
        "manager",
        "leader-trust",
        status="working",
        provider="codex",
    )
    await db.execute(
        "UPDATE sessions SET tmux_pane = ?, tmux_session = ? WHERE id = ?",
        ("%21", "atc", session.id),
    )
    await db.execute(
        "UPDATE leaders SET session_id = ?, context = ? WHERE id = ?",
        (
            session.id,
            json.dumps({"leader_kickoff_payload": {"message": "go"}}),
            leader.id,
        ),
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
                readiness=ReadinessState.BLOCKED,
                block_reason=RuntimeBlockReason.TRUST,
                summary="Do you trust this directory?",
            )
        ),
    )

    assert health.runtime_state == "blocked"
    assert health.leader_state == "blocked_on_provider_prompt"
    assert health.current_blocker == "runtime_trust_required"
    assert health.kickoff_state["kickoff_state"] == "blocked_on_provider_prompt"
    assert health.operator_guidance["recommended_action"] == (
        "resolve_startup_trust_prompt_before_nudge"
    )
    assert "Do not send goal nudges" in health.operator_guidance["details"]


@pytest.mark.asyncio
async def test_leader_health_unverified_startup_checks_trust_before_progress_nudge(db) -> None:
    project = await create_project(db, "leader-unverified-startup")
    leader = await create_leader(db, project.id, goal="Check health first")
    session = await create_session(
        db,
        project.id,
        "manager",
        "leader-unverified",
        status="working",
        provider="codex",
    )
    await db.execute(
        "UPDATE sessions SET tmux_pane = ?, tmux_session = ? WHERE id = ?",
        ("%22", "atc", session.id),
    )
    await db.execute(
        "UPDATE leaders SET session_id = ?, context = ? WHERE id = ?",
        (
            session.id,
            json.dumps({"leader_kickoff_payload": {"message": "go"}}),
            leader.id,
        ),
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
                readiness=ReadinessState.READY,
                summary="Ready",
            )
        ),
    )

    assert health.leader_state == "kickoff_unverified"
    assert health.kickoff_state["kickoff_state"] == "kickoff_unverified"
    assert health.operator_guidance["recommended_action"] == (
        "check_startup_trust_prompt_before_nudge"
    )
    assert "folder trust/startup prompt may appear" in health.operator_guidance["details"]


@pytest.mark.asyncio
async def test_ace_health_treats_startup_trust_as_expected_pre_instruction_branch(db) -> None:
    project = await create_project(db, "ace-trust-startup")
    task = await create_task_graph(db, project.id, "Task one")
    ace = await create_session(db, project.id, "ace", "ace-one", status="waiting", task_id=task.id)
    await db.execute(
        "UPDATE sessions SET tmux_pane = ?, tmux_session = ? WHERE id = ?",
        ("%19", "atc", ace.id),
    )
    assignment, _ = await assign_task(db, task.id, ace.id, f"ace:{task.id}")
    await update_task_assignment_dispatch(
        db,
        assignment.assignment_id,
        dispatch_delivery_state="submitted",
        dispatch_verified=False,
        blocker_reason="runtime_trust_required",
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
                block_reason=RuntimeBlockReason.TRUST,
                summary="Do you trust this directory?",
            )
        ),
    )

    data = health.as_dict()
    assert data["runtime_state"] == "blocked"
    assert data["current_blocker"] == "runtime_trust_required"
    assert data["operator_guidance"]["recommended_action"] == (
        "resolve_ace_startup_trust_prompt_before_assignment_nudge"
    )
    assert "expected Ace-start branch" in data["operator_guidance"]["details"]
    assert "Do not resend task instructions" in data["operator_guidance"]["details"]


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
async def test_ace_report_active_records_assignment_acceptance_and_health(db) -> None:
    project = await create_project(db, "ace-report-active-proj")
    task = await create_task_graph(db, project.id, "Task one")
    ace = await create_session(db, project.id, "ace", "ace-one", status="waiting", task_id=task.id)
    await db.execute(
        "UPDATE sessions SET tmux_pane = ?, tmux_session = ? WHERE id = ?",
        ("%10", "atc", ace.id),
    )
    assignment, _ = await assign_task(db, task.id, ace.id, f"ace:{task.id}")
    await update_task_assignment_dispatch(
        db,
        assignment.assignment_id,
        dispatch_delivery_state="accepted_active",
        dispatch_verified=True,
        last_activity=True,
    )

    result = await report_ace_active(
        project.id,
        ace.id,
        AceActiveReportRequest(
            assignment_id=assignment.assignment_id,
            message="accepted and working",
        ),
        _request(db),
    )

    assert result["assignment_accepted"] is True
    assert result["ace_reported_active"] is True
    assert result["assignment_acceptance_state"] == "assignment_accepted"
    updated_session = await get_session(db, ace.id)
    assert updated_session is not None
    assert updated_session.status == "working"

    health = await ace_health(
        db,
        project.id,
        ace.id,
        runtime_service=FakeRuntimeService(
            RuntimeInspection(
                session_id=ace.id,
                provider_name="codex",
                alive=True,
                readiness=ReadinessState.BUSY,
                summary="working",
            )
        ),
    )
    dispatch = health.as_dict()["ace_dispatch"]
    assert dispatch["assignment_acceptance_state"] == "accepted_active"
    assert dispatch["assignment_accepted"] is True
    assert dispatch["ace_reported_active"] is True
    assert dispatch["acceptance_message"] == "accepted and working"
    assert health.operator_guidance["severity"] == "ok"


@pytest.mark.asyncio
async def test_ace_health_warns_when_delivery_active_but_ace_has_not_reported(db) -> None:
    project = await create_project(db, "ace-waiting-report-proj")
    task = await create_task_graph(db, project.id, "Task one")
    ace = await create_session(db, project.id, "ace", "ace-one", status="working", task_id=task.id)
    await db.execute(
        "UPDATE sessions SET tmux_pane = ?, tmux_session = ? WHERE id = ?",
        ("%11", "atc", ace.id),
    )
    assignment, _ = await assign_task(db, task.id, ace.id, f"ace:{task.id}")
    await update_task_assignment_dispatch(
        db,
        assignment.assignment_id,
        dispatch_delivery_state="accepted_active",
        dispatch_verified=True,
        last_activity=True,
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
                readiness=ReadinessState.BUSY,
                summary="working",
            )
        ),
    )

    assert health.ace_dispatch["assignment_acceptance_state"] == "awaiting_ace_active_report"
    assert health.operator_guidance["severity"] == "warning"
    assert health.operator_guidance["recommended_action"] == (
        "wait_for_ace_report_active_or_inspect_runtime"
    )


@pytest.mark.asyncio
async def test_leader_health_surfaces_ace_blocker_without_tower_ace_inspection(db) -> None:
    project = await create_project(db, "leader-owned-ace-blocker")
    leader = await create_leader(db, project.id, goal="Coordinate Aces")
    session = await create_session(
        db,
        project.id,
        "manager",
        "leader",
        status="working",
        provider="codex",
    )
    await db.execute(
        "UPDATE sessions SET tmux_pane = ?, tmux_session = ? WHERE id = ?",
        ("%13", "atc", session.id),
    )
    await db.execute("UPDATE leaders SET session_id = ? WHERE id = ?", (session.id, leader.id))
    task = await create_task_graph(db, project.id, "Blocked task")
    ace = await create_session(
        db, project.id, "ace", "ace-blocked", status="waiting", task_id=task.id
    )
    assignment, _ = await assign_task(db, task.id, ace.id, f"{leader.id}:{task.id}")
    await update_task_assignment_dispatch(
        db,
        assignment.assignment_id,
        dispatch_delivery_state="blocked",
        dispatch_verified=False,
        blocker_reason="ace_dispatch_failed",
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
                readiness=ReadinessState.READY,
                summary="ready",
            )
        ),
    )

    assert health.runtime_state == "ready"
    assert health.ace_dispatch["blocked"] == 1
    assert health.ace_dispatch["unverified"] == 1
    assert health.current_blocker == "ace_dispatch_failed"
    assert health.recovery_recommendation is not None
    assert health.recovery_recommendation["command"].startswith("atc leader recover")
    assert health.operator_guidance["severity"] == "blocked"
    assert health.operator_guidance["recommended_action"] == "inspect_runtime_blocker"


@pytest.mark.asyncio
async def test_activity_timestamps_prefer_assignment_activity_for_leader_and_ace_health(db) -> None:
    project = await create_project(db, "activity-timestamps")
    leader = await create_leader(db, project.id, goal="Track activity")
    leader_session = await create_session(
        db,
        project.id,
        "manager",
        "leader",
        status="working",
        provider="codex",
    )
    await db.execute(
        "UPDATE sessions SET tmux_pane = ?, tmux_session = ?, updated_at = ? WHERE id = ?",
        ("%14", "atc", "2026-06-12T12:00:00+00:00", leader_session.id),
    )
    await db.execute(
        "UPDATE leaders SET session_id = ?, context = ? WHERE id = ?",
        (leader_session.id, json.dumps({"leader_kickoff_payload": {"message": "go"}}), leader.id),
    )
    task = await create_task_graph(db, project.id, "Active task")
    ace = await create_session(
        db, project.id, "ace", "ace-active", status="working", task_id=task.id
    )
    await db.execute(
        "UPDATE sessions SET tmux_pane = ?, tmux_session = ?, updated_at = ? WHERE id = ?",
        ("%15", "atc", "2026-06-12T12:01:00+00:00", ace.id),
    )
    assignment, _ = await assign_task(db, task.id, ace.id, f"activity:{task.id}")
    await update_task_assignment_dispatch(
        db,
        assignment.assignment_id,
        dispatch_delivery_state="accepted_active",
        dispatch_verified=True,
    )
    await db.execute(
        "UPDATE task_assignments SET last_activity_at = ? WHERE assignment_id = ?",
        ("2026-06-12T12:05:00+00:00", assignment.assignment_id),
    )
    await db.commit()

    runtime_service = FakeRuntimeService(
        RuntimeInspection(
            session_id=leader_session.id,
            provider_name="codex",
            alive=True,
            readiness=ReadinessState.BUSY,
            summary="working",
        )
    )
    leader_data = await leader_health(db, project.id, runtime_service=runtime_service)
    ace_data = await ace_health(db, project.id, ace.id, runtime_service=runtime_service)

    assert leader_data.last_activity_at == "2026-06-12T12:05:00+00:00"
    assert ace_data.last_activity_at == "2026-06-12T12:05:00+00:00"


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
async def test_prompt_not_submitted_recovery_requires_persisted_payload_match(db) -> None:
    project = await create_project(db, "pending-prompt-proj")
    leader = await create_leader(db, project.id, goal="Recover pending prompt")
    session = await create_session(
        db,
        project.id,
        "manager",
        "leader-pending",
        status="working",
        provider="codex",
    )
    message = "# Mission Brief\n\nRecover pending prompt"
    await db.execute(
        "UPDATE sessions SET tmux_pane = ?, tmux_session = ? WHERE id = ?",
        ("%21", "atc", session.id),
    )
    await db.execute(
        "UPDATE leaders SET session_id = ?, context = ? WHERE id = ?",
        (
            session.id,
            json.dumps(
                {
                    "leader_kickoff_payload": {
                        "project_id": project.id,
                        "goal": "Recover pending prompt",
                        "message": message,
                        "source": "test",
                    }
                }
            ),
            leader.id,
        ),
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
                readiness=ReadinessState.READY,
                summary="Codex prompt contains visible unsubmitted text",
                details={
                    "blocker_reason": BlockerReason.PROMPT_NOT_SUBMITTED.value,
                    "provider_diagnostics": {
                        "pending_prompt_text": "Recover pending prompt"
                    },
                },
            )
        ),
    )

    assert health.current_blocker == BlockerReason.PROMPT_NOT_SUBMITTED.value
    assert health.kickoff_state["pending_prompt_observed"] is True
    assert health.kickoff_state["pending_prompt_matches_persisted_payload"] is True
    dry_run = build_recovery_plan(health, mode="dry_run")
    assert dry_run.safe_to_apply is True
    assert dry_run.actions[1]["action"] == "submit_pending_prompt"
    assert dry_run.actions[1]["policy_required"] == "submit_pending_prompt"
    apply_without_policy = build_recovery_plan(health, mode="apply")
    assert (
        apply_without_policy.refused_reason
        == "apply_requires_submit_pending_prompt_policy"
    )


@pytest.mark.asyncio
async def test_prompt_not_submitted_apply_reinspects_and_submits_via_provider(
    db, monkeypatch
) -> None:
    project = await create_project(db, "pending-prompt-apply-proj")
    leader = await create_leader(db, project.id, goal="Apply pending prompt")
    session = await create_session(
        db,
        project.id,
        "manager",
        "leader-pending-apply",
        status="working",
        provider="codex",
    )
    await db.execute(
        "UPDATE sessions SET tmux_pane = ?, tmux_session = ? WHERE id = ?",
        ("%22", "atc", session.id),
    )
    await db.execute(
        "UPDATE leaders SET session_id = ?, context = ? WHERE id = ?",
        (
            session.id,
            json.dumps(
                {
                    "leader_kickoff_payload": {
                        "project_id": project.id,
                        "goal": "Apply pending prompt",
                        "message": "# Mission Brief\n\nApply pending prompt",
                        "source": "test",
                    }
                }
            ),
            leader.id,
        ),
    )
    await db.commit()
    inspection = RuntimeInspection(
        session_id=session.id,
        provider_name="codex",
        alive=True,
        readiness=ReadinessState.READY,
        summary="Codex prompt contains visible unsubmitted text",
        details={
            "blocker_reason": BlockerReason.PROMPT_NOT_SUBMITTED.value,
            "provider_diagnostics": {"pending_prompt_text": "Apply pending prompt"},
        },
    )
    fake_service = FakeRuntimeService(inspection)
    health = await leader_health(db, project.id, runtime_service=fake_service)

    import atc.runtime.health as health_module

    async def fake_leader_health(conn, project_id, *, runtime_service=None):
        return health

    monkeypatch.setattr(health_module, "RuntimeService", lambda: fake_service)
    monkeypatch.setattr(health_module, "leader_health", fake_leader_health)
    event_bus = FakeEventBus()

    plan = await apply_recovery_plan(
        db,
        health,
        policy="submit_pending_prompt",
        event_bus=event_bus,
    )

    assert plan.refused_reason is None
    assert fake_service.submitted is True
    assert plan.actions[-1]["action"] == "submit_pending_prompt"
    assert plan.actions[-1]["status"] == "applied"
    assert event_bus.events == [
        (
            "runtime_recovery_audit",
            {
                "role": "leader",
                "project_id": project.id,
                "session_id": session.id,
                "blocker_reason": "prompt_not_submitted",
                "policy": "submit_pending_prompt",
                "status": "applied",
                "action": "submit_pending_prompt",
            },
        )
    ]


@pytest.mark.asyncio
async def test_prompt_not_submitted_apply_audits_policy_refusal(db) -> None:
    project = await create_project(db, "pending-prompt-refuse-audit")
    health = RuntimeHealth(
        role="leader",
        project_id=project.id,
        runtime_exists=True,
        pane_attached=True,
        provider="codex",
        session_id="leader-session",
        runtime_state=RuntimeState.BLOCKED.value,
        current_blocker=BlockerReason.PROMPT_NOT_SUBMITTED.value,
        kickoff_state={"pending_prompt_matches_persisted_payload": True},
    )
    event_bus = FakeEventBus()

    plan = await apply_recovery_plan(db, health, policy="inspect_first", event_bus=event_bus)

    assert plan.refused_reason == "apply_requires_submit_pending_prompt_policy"
    assert event_bus.events == [
        (
            "runtime_recovery_audit",
            {
                "role": "leader",
                "project_id": project.id,
                "session_id": "leader-session",
                "blocker_reason": "prompt_not_submitted",
                "policy": "inspect_first",
                "status": "refused",
                "refused_reason": "apply_requires_submit_pending_prompt_policy",
            },
        )
    ]


@pytest.mark.asyncio
async def test_update_prompt_recovery_is_capability_and_policy_gated(db) -> None:
    project = await create_project(db, "update-policy-proj")
    health = RuntimeHealth(
        role="leader",
        project_id=project.id,
        runtime_exists=True,
        pane_attached=True,
        provider="codex",
        session_id="leader-session",
        runtime_state=RuntimeState.BLOCKED.value,
        current_blocker=BlockerReason.RUNTIME_UPDATE_REQUIRED.value,
        provider_diagnostics={
            "details": {
                "recovery_capabilities": {
                    "can_detect_update_prompt": True,
                    "can_accept_update_prompt": True,
                    "requires_fresh_session_after_update": True,
                }
            }
        },
    )

    dry_run = build_recovery_plan(health, mode="dry_run")
    assert dry_run.safe_to_apply is True
    assert [a["action"] for a in dry_run.actions] == [
        "inspect_runtime",
        "provider_update_required",
        "accept_provider_update",
        "restart_required",
    ]

    apply_without_policy = build_recovery_plan(health, mode="apply")
    assert apply_without_policy.refused_reason == "apply_requires_update_policy"

    apply_with_policy = await apply_recovery_plan(
        db,
        health,
        policy="auto_accept_updates_and_restart",
    )
    assert apply_with_policy.refused_reason == "provider_update_apply_not_implemented"


@pytest.mark.asyncio
async def test_provider_blocker_reason_flows_from_inspection_to_recovery_plan(db) -> None:
    project = await create_project(db, "provider-blocker-proj")
    leader = await create_leader(db, project.id, goal="Recover update")
    session = await create_session(
        db,
        project.id,
        "manager",
        "leader-update",
        status="working",
        provider="codex",
    )
    await db.execute(
        "UPDATE sessions SET tmux_pane = ?, tmux_session = ? WHERE id = ?",
        ("%12", "atc", session.id),
    )
    await db.execute("UPDATE leaders SET session_id = ? WHERE id = ?", (session.id, leader.id))
    await db.commit()

    health = await leader_health(
        db,
        project.id,
        runtime_service=FakeRuntimeService(
            RuntimeInspection(
                session_id=session.id,
                provider_name="codex",
                alive=True,
                readiness=ReadinessState.BLOCKED,
                block_reason=RuntimeBlockReason.PROVIDER_PROMPT,
                summary="Codex runtime update prompt visible",
                details={
                    "blocker_reason": BlockerReason.RUNTIME_UPDATE_REQUIRED.value,
                    "recovery_capabilities": {
                        "can_detect_update_prompt": True,
                        "can_accept_update_prompt": False,
                        "requires_fresh_session_after_update": True,
                    },
                },
            )
        ),
    )

    assert health.current_blocker == BlockerReason.RUNTIME_UPDATE_REQUIRED.value
    plan = build_recovery_plan(health, mode="dry_run")
    assert plan.actions[1]["action"] == "provider_update_required"
    assert plan.safe_to_apply is False


@pytest.mark.asyncio
async def test_restart_policy_must_match_missing_vs_stale_reason(db) -> None:
    project = await create_project(db, "restart-policy-proj")

    missing = RuntimeHealth(
        role="leader",
        project_id=project.id,
        runtime_exists=False,
        pane_attached=False,
        provider="codex",
        runtime_state=RuntimeState.MISSING.value,
        current_blocker=BlockerReason.PANE_MISSING.value,
    )
    stale = RuntimeHealth(
        role="leader",
        project_id=project.id,
        runtime_exists=True,
        pane_attached=True,
        provider="codex",
        runtime_state=RuntimeState.STALE.value,
        current_blocker=BlockerReason.STALE_AFTER_UPDATE.value,
    )

    assert (
        build_recovery_plan(missing, mode="apply", policy="restart_stale_runtime").refused_reason
        == "apply_requires_explicit_policy"
    )
    assert (
        build_recovery_plan(
            missing,
            mode="apply",
            policy="auto_accept_updates_and_restart",
        ).refused_reason
        == "apply_requires_explicit_policy"
    )
    assert (
        build_recovery_plan(stale, mode="apply", policy="restart_missing_pane").refused_reason
        == "apply_requires_explicit_policy"
    )
    assert (
        build_recovery_plan(missing, mode="apply", policy="restart_missing_pane").refused_reason
        is None
    )
    assert (
        build_recovery_plan(stale, mode="apply", policy="restart_stale_runtime").refused_reason
        is None
    )


@pytest.mark.asyncio
async def test_stale_leader_restart_apply_uses_persisted_goal(db, monkeypatch) -> None:
    project = await create_project(db, "stale-restart-proj")
    await create_leader(db, project.id, goal="Recover from stale runtime")
    calls = {}

    async def fake_stop(conn, project_id, *, event_bus=None):
        calls["stopped"] = project_id

    async def fake_start(conn, project_id, *, goal=None, event_bus=None, context_package=None):
        calls["started"] = {"project_id": project_id, "goal": goal}
        return "new-leader-session"

    async def fake_health(conn, project_id, *, runtime_service=None):
        return RuntimeHealth(
            role="leader",
            project_id=project_id,
            runtime_exists=True,
            pane_attached=True,
            provider="codex",
            session_id="new-leader-session",
            runtime_state=RuntimeState.READY.value,
        )

    import atc.runtime.health as health_module
    from atc.leader import leader as leader_ops

    monkeypatch.setattr(leader_ops, "stop_leader", fake_stop)
    monkeypatch.setattr(leader_ops, "start_leader", fake_start)
    monkeypatch.setattr(health_module, "leader_health", fake_health)

    plan = await apply_recovery_plan(
        db,
        RuntimeHealth(
            role="leader",
            project_id=project.id,
            runtime_exists=False,
            pane_attached=False,
            provider="codex",
            runtime_state=RuntimeState.MISSING.value,
            current_blocker=BlockerReason.PANE_MISSING.value,
            kickoff_state={"original_goal_available": True},
        ),
        policy="restart_missing_pane",
    )

    assert plan.refused_reason is None
    assert calls == {
        "stopped": project.id,
        "started": {"project_id": project.id, "goal": "Recover from stale runtime"},
    }
    assert plan.actions[-1]["action"] == "restart_leader"


@pytest.mark.asyncio
async def test_stale_leader_restart_tolerates_missing_pane_stop_failure(db, monkeypatch) -> None:
    project = await create_project(db, "stop-failure-proj")
    leader = await create_leader(db, project.id, goal="Recover despite missing pane")
    session = await create_session(
        db,
        project.id,
        "manager",
        "leader-stale",
        status="working",
        provider="codex",
    )
    await db.execute(
        "UPDATE sessions SET tmux_pane = ?, tmux_session = ? WHERE id = ?",
        ("%missing", "atc", session.id),
    )
    await db.execute("UPDATE leaders SET session_id = ? WHERE id = ?", (session.id, leader.id))
    await db.commit()

    async def fake_stop(conn, project_id, *, event_bus=None):
        raise RuntimeError("no such pane: %missing")

    async def fake_start(conn, project_id, *, goal=None, event_bus=None, context_package=None):
        return "replacement-session"

    async def fake_health(conn, project_id, *, runtime_service=None):
        return RuntimeHealth(
            role="leader",
            project_id=project_id,
            runtime_exists=True,
            pane_attached=True,
            provider="codex",
            session_id="replacement-session",
            runtime_state=RuntimeState.READY.value,
        )

    import atc.runtime.health as health_module
    from atc.leader import leader as leader_ops

    monkeypatch.setattr(leader_ops, "stop_leader", fake_stop)
    monkeypatch.setattr(leader_ops, "start_leader", fake_start)
    monkeypatch.setattr(health_module, "leader_health", fake_health)

    plan = await apply_recovery_plan(
        db,
        RuntimeHealth(
            role="leader",
            project_id=project.id,
            runtime_exists=True,
            pane_attached=True,
            provider="codex",
            session_id=session.id,
            runtime_state=RuntimeState.STALE.value,
            current_blocker=BlockerReason.STALE_AFTER_UPDATE.value,
        ),
        policy="restart_stale_runtime",
    )

    stale_session = await get_session(db, session.id)
    assert plan.refused_reason is None
    assert any(action["action"] == "stop_stale_leader" for action in plan.actions)
    assert stale_session is not None
    assert stale_session.status == "disconnected"
    assert plan.actions[-1]["session_id"] == "replacement-session"


@pytest.mark.asyncio
async def test_stale_leader_restart_refuses_non_stale_stop_failure(db, monkeypatch) -> None:
    project = await create_project(db, "stop-db-failure-proj")
    await create_leader(db, project.id, goal="Do not split brain")
    calls = {"started": False}

    async def fake_stop(conn, project_id, *, event_bus=None):
        raise RuntimeError("database locked")

    async def fake_start(conn, project_id, *, goal=None, event_bus=None, context_package=None):
        calls["started"] = True
        return "should-not-start"

    from atc.leader import leader as leader_ops

    monkeypatch.setattr(leader_ops, "stop_leader", fake_stop)
    monkeypatch.setattr(leader_ops, "start_leader", fake_start)

    plan = await apply_recovery_plan(
        db,
        RuntimeHealth(
            role="leader",
            project_id=project.id,
            runtime_exists=True,
            pane_attached=True,
            provider="codex",
            runtime_state=RuntimeState.STALE.value,
            current_blocker=BlockerReason.STALE_AFTER_UPDATE.value,
        ),
        policy="restart_stale_runtime",
    )

    assert plan.refused_reason == "stop_leader_failed"
    assert calls["started"] is False
    assert plan.actions[-1]["action"] == "stop_leader"


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

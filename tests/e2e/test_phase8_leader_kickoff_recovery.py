"""Phase 8 Leader kickoff recovery scenario regressions.

These tests supplement the broader Phase 8 runtime/orchestration suite with the
field-failure seams from Leader kickoff recovery: session rows and submitted
transport states are not execution proof; ATC needs kickoff acceptance, task graph
truth, and Leader-owned Ace dispatch evidence.
"""

from __future__ import annotations

import json

import pytest

from atc.agents.deploy import (
    AceDeploySpec,
    ManagerDeploySpec,
    deploy_ace_files,
    deploy_manager_files,
)
from atc.leader.kickoff import report_leader_goal_accepted
from atc.runtime.health import (
    ace_health,
    apply_recovery_plan,
    build_recovery_plan,
    leader_health,
)
from atc.runtime.models import BlockerReason, ReadinessState, RuntimeInspection, RuntimeState
from atc.state.db import (
    _SCHEMA_SQL,
    assign_task,
    create_leader,
    create_project,
    create_session,
    create_task_graph,
    get_connection,
    report_ace_assignment_active,
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
    def __init__(self, inspection: RuntimeInspection) -> None:
        self.inspection = inspection
        self.submitted = False

    async def inspect_session_record(self, _session):
        return self.inspection

    async def submit_pending_prompt_for_session_record(self, _session, _inspection):
        self.submitted = True
        return True


class FakeEventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))


async def _leader_with_session(db, *, goal: str = "Build the thing"):
    project = await create_project(db, "phase8-kickoff-project")
    leader = await create_leader(db, project.id, goal=goal)
    session = await create_session(
        db,
        project.id,
        "manager",
        "phase8-leader",
        status="working",
        provider="codex",
    )
    await db.execute(
        "UPDATE sessions SET tmux_pane = ?, tmux_session = ? WHERE id = ?",
        ("%phase8", "atc", session.id),
    )
    await db.execute(
        "UPDATE leaders SET session_id = ?, context = ? WHERE id = ?",
        (
            session.id,
            json.dumps(
                {
                    "leader_kickoff_payload": {
                        "project_id": project.id,
                        "goal": goal,
                        "message": f"# Mission Brief\n\n{goal}",
                        "source": "phase8-test",
                    },
                    "leader_original_goal": goal,
                }
            ),
            leader.id,
        ),
    )
    await db.commit()
    return project, leader, session


@pytest.mark.asyncio
async def test_phase8_session_row_is_not_execution_proof(db) -> None:
    """A Leader session can exist while kickoff is still blocked/unaccepted."""

    project, _leader, session = await _leader_with_session(db)

    health = await leader_health(
        db,
        project.id,
        runtime_service=FakeRuntimeService(
            RuntimeInspection(
                session_id=session.id,
                provider_name="codex",
                alive=True,
                readiness=ReadinessState.BLOCKED,
                summary="managed workspace trust prompt",
                details={"blocker_reason": BlockerReason.RUNTIME_TRUST_REQUIRED.value},
            )
        ),
    )

    assert health.runtime_exists is True
    assert health.runtime_state == RuntimeState.BLOCKED.value
    assert health.leader_state == "blocked_on_provider_prompt"
    assert health.kickoff_state["kickoff_verified"] is False
    assert health.kickoff_state["goal_acceptance_state"] == "submitted_pending_acceptance"
    assert health.current_blocker == BlockerReason.RUNTIME_TRUST_REQUIRED.value
    assert health.operator_guidance["recommended_action"] == (
        "resolve_startup_trust_prompt_before_nudge"
    )


@pytest.mark.asyncio
async def test_phase8_prompt_not_submitted_recovery_is_audited(db, monkeypatch) -> None:
    project, _leader, session = await _leader_with_session(db, goal="Submit pending prompt")
    inspection = RuntimeInspection(
        session_id=session.id,
        provider_name="codex",
        alive=True,
        readiness=ReadinessState.READY,
        summary="Provider has pending text in the input buffer",
        details={
            "blocker_reason": BlockerReason.PROMPT_NOT_SUBMITTED.value,
            "provider_diagnostics": {"pending_prompt_text": "Submit pending prompt"},
        },
    )
    service = FakeRuntimeService(inspection)
    health = await leader_health(db, project.id, runtime_service=service)

    dry_run = build_recovery_plan(health, mode="dry_run")
    assert dry_run.safe_to_apply is True
    assert [action["action"] for action in dry_run.actions] == [
        "inspect_runtime",
        "submit_pending_prompt",
    ]

    import atc.runtime.health as health_module

    async def fake_leader_health(conn, project_id, *, runtime_service=None):
        return health

    monkeypatch.setattr(health_module, "RuntimeService", lambda: service)
    monkeypatch.setattr(health_module, "leader_health", fake_leader_health)
    event_bus = FakeEventBus()

    applied = await apply_recovery_plan(
        db,
        health,
        policy="submit_pending_prompt",
        event_bus=event_bus,
    )

    assert applied.refused_reason is None
    assert service.submitted is True
    assert applied.actions[-1]["status"] == "applied"
    assert event_bus.events == [
        (
            "runtime_recovery_audit",
            {
                "role": "leader",
                "project_id": project.id,
                "session_id": session.id,
                "blocker_reason": BlockerReason.PROMPT_NOT_SUBMITTED.value,
                "policy": "submit_pending_prompt",
                "status": "applied",
                "action": "submit_pending_prompt",
            },
        )
    ]


@pytest.mark.asyncio
async def test_phase8_leader_acceptance_requires_report_and_task_graph_truth(db) -> None:
    project, _leader, session = await _leader_with_session(db, goal="Create actionable tasks")

    before = await leader_health(
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
    assert before.leader_state == "kickoff_unverified"
    assert before.kickoff_state["kickoff_verified"] is False

    await report_leader_goal_accepted(
        db,
        project_id=project.id,
        goal_accepted=True,
        message="accepted and decomposing",
    )
    await create_task_graph(
        db,
        project.id,
        "Implement feed UI",
        description="Build the initial page",
    )
    await db.commit()

    after = await leader_health(
        db,
        project.id,
        runtime_service=FakeRuntimeService(
            RuntimeInspection(
                session_id=session.id,
                provider_name="codex",
                alive=True,
                readiness=ReadinessState.READY,
                summary="Ready after task graph creation",
            )
        ),
    )

    assert after.leader_state == "working"
    assert after.kickoff_state["leader_reported_active"] is True
    assert after.kickoff_state["goal_accepted"] is True
    assert after.kickoff_state["first_actionable_step_observed_at"] is not None
    assert after.kickoff_state["kickoff_verified"] is True
    assert after.operator_guidance["severity"] == "ok"


@pytest.mark.asyncio
async def test_phase8_tower_to_leader_to_ace_chain_uses_assignment_truth(db) -> None:
    project, leader, session = await _leader_with_session(db, goal="Coordinate Ace work")
    await report_leader_goal_accepted(db, project_id=project.id, goal_accepted=True)
    task = await create_task_graph(db, project.id, "Build card component")
    ace = await create_session(
        db,
        project.id,
        "ace",
        "ace-card-component",
        status="working",
        provider="codex",
        task_id=task.id,
    )
    await db.execute(
        "UPDATE sessions SET tmux_pane = ?, tmux_session = ? WHERE id = ?",
        ("%ace-phase8", "atc", ace.id),
    )
    assignment, _created = await assign_task(db, task.id, ace.id, f"{leader.id}:{task.id}")
    await update_task_assignment_dispatch(
        db,
        assignment.assignment_id,
        dispatch_delivery_state="accepted_active",
        dispatch_verified=True,
        last_activity=True,
    )
    await report_ace_assignment_active(
        db,
        assignment.assignment_id,
        accepted=True,
        message="accepted task",
        last_activity=True,
    )
    await db.commit()

    leader_state = await leader_health(
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
    ace_state = await ace_health(
        db,
        project.id,
        ace.id,
        runtime_service=FakeRuntimeService(
            RuntimeInspection(
                session_id=ace.id,
                provider_name="codex",
                alive=True,
                readiness=ReadinessState.BUSY,
                summary="Ace working",
            )
        ),
    )

    assert leader_state.leader_state == "working"
    assert leader_state.task_graph_state["total"] == 1
    assert leader_state.ace_dispatch["verified"] == 1
    assert ace_state.runtime_exists is True
    assert ace_state.ace_dispatch["assignment_id"] == assignment.assignment_id
    assert ace_state.ace_dispatch["dispatch_delivery_state"] == "accepted_active"
    assert ace_state.ace_dispatch["assignment_acceptance_state"] == "accepted_active"
    assert ace_state.ace_dispatch["assignment_accepted"] is True


@pytest.mark.asyncio
async def test_phase8_ace_blocker_truth_stays_leader_owned_and_classified(db) -> None:
    project, leader, _session = await _leader_with_session(db, goal="Surface Ace blockers")
    task = await create_task_graph(db, project.id, "Connect Instagram API")
    ace = await create_session(
        db,
        project.id,
        "ace",
        "ace-instagram-api",
        status="waiting",
        provider="codex",
        task_id=task.id,
    )
    await db.execute(
        "UPDATE sessions SET tmux_pane = ?, tmux_session = ? WHERE id = ?",
        ("%ace-blocked", "atc", ace.id),
    )
    assignment, _created = await assign_task(db, task.id, ace.id, f"{leader.id}:{task.id}")
    await update_task_assignment_dispatch(
        db,
        assignment.assignment_id,
        dispatch_delivery_state="blocked",
        dispatch_verified=False,
        blocker_reason=BlockerReason.RUNTIME_PERMISSION_REQUIRED.value,
    )
    await db.commit()

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
                summary="permission prompt",
                details={"blocker_reason": BlockerReason.RUNTIME_PERMISSION_REQUIRED.value},
            )
        ),
    )

    assert health.current_blocker == BlockerReason.RUNTIME_PERMISSION_REQUIRED.value
    assert health.ace_dispatch["assignment_id"] == assignment.assignment_id
    assert health.ace_dispatch["dispatch_verified"] is False
    assert health.ace_dispatch["assignment_acceptance_state"] == "blocked"
    assert health.operator_guidance["recommended_action"] == "inspect_runtime_blocker"


def test_phase8_local_atc_api_capability_is_deployed_for_managed_agents(tmp_path) -> None:
    manager = deploy_manager_files(
        ManagerDeploySpec(
            leader_id="leader-1",
            session_id="session-1",
            project_name="Phase 8",
            goal="Inspect local ATC API",
            project_id="project-1",
            api_base_url="http://127.0.0.1:8420",
        ),
        staging_root=tmp_path,
    )
    ace = deploy_ace_files(
        AceDeploySpec(
            session_id="ace-1",
            project_name="Phase 8",
            task_title="Inspect local API",
            project_id="project-1",
            api_base_url="http://127.0.0.1:8420",
        ),
        staging_root=tmp_path,
    )

    for deployed in (manager, ace):
        capability = json.loads(deployed.local_api_capability_path.read_text())
        helper = deployed.local_api_helper_path.read_text()
        assert capability["external_network_allowed"] is False
        assert capability["host_allowlist"] == ["127.0.0.1", "localhost"]
        assert "/openapi.json" in capability["path_prefixes"]
        assert "/api/projects" in capability["path_prefixes"]
        assert "CAPABILITY_FILE" in helper
        assert "urllib.request" in helper

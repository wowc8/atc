"""Tests for Leader start kickoff verification API output."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atc.runtime.models import DeliveryState, RoleKind, RuntimeDeliveryResult, RuntimeState
from atc.state.db import _SCHEMA_SQL, create_leader, create_project, get_connection, run_migrations


@pytest.fixture
async def db():
    await run_migrations(":memory:")
    async with get_connection(":memory:") as conn:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
        yield conn


@pytest.fixture
def mock_request(db):
    request = MagicMock()
    request.app.state.db = db
    request.app.state.event_bus = None
    return request


@pytest.mark.asyncio
async def test_leader_start_returns_verified_kickoff_and_persists_payload(
    db,
    mock_request,
) -> None:
    from atc.api.routers.projects import LeaderStartRequest, start_leader

    project = await create_project(db, "phase3", description="verify startup")
    await create_leader(db, project.id)
    delivery = RuntimeDeliveryResult(
        session_id="leader-session-1",
        provider_name="codex",
        role=RoleKind.LEADER,
        status="confirmed",
        stage="agent_output_observed",
        verdict="confirmed",
        reason_code="agent_output",
        runtime_state=RuntimeState.ACTIVE,
        delivery_state=DeliveryState.ACCEPTED_ACTIVE,
    )

    with (
        patch(
            "atc.api.routers.projects.leader_ops.start_leader",
            new=AsyncMock(return_value="leader-session-1"),
        ),
        patch("atc.leader.leader.send_leader_message", new=AsyncMock(return_value=delivery)),
    ):
        response = await start_leader(
            project.id,
            LeaderStartRequest(goal="Build kickoff verification"),
            mock_request,
        )

    assert response["kickoff_verified"] is True
    assert response["kickoff_state"] == "accepted_active"
    assert response["startup_handshake_state"] == "ready"
    assert response["goal_acceptance_state"] == "accepted_active"
    assert response["truth_delivery_state"] == "accepted_active"
    assert response["runtime_state"] == "active"
    assert response["kickoff_payload_persisted"] is True

    cursor = await db.execute("SELECT context FROM leaders WHERE project_id = ?", (project.id,))
    context_json = (await cursor.fetchone())[0]
    context = json.loads(context_json)
    assert context["leader_original_goal"] == "Build kickoff verification"
    assert context["leader_kickoff_payload"]["message"].startswith("# Mission Brief")
    assert context["leader_kickoff_payload"]["trace_id"]


@pytest.mark.asyncio
async def test_leader_start_without_auto_kickoff_still_persists_goal_payload(
    db,
    mock_request,
) -> None:
    from atc.api.routers.projects import LeaderStartRequest, start_leader

    project = await create_project(db, "phase3-no-kickoff")
    await create_leader(db, project.id)

    with patch(
        "atc.api.routers.projects.leader_ops.start_leader",
        new=AsyncMock(return_value="leader-session-1"),
    ):
        response = await start_leader(
            project.id,
            LeaderStartRequest(goal="Recover me later", auto_kickoff=False),
            mock_request,
        )

    assert response["kickoff_verified"] is False
    assert response["kickoff_state"] == "not_requested"
    assert response["kickoff_payload_persisted"] is True
    cursor = await db.execute("SELECT context FROM leaders WHERE project_id = ?", (project.id,))
    context_json = (await cursor.fetchone())[0]
    assert json.loads(context_json)["leader_kickoff_payload"]["trace_id"]

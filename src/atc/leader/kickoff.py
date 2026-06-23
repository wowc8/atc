"""Leader kickoff payload persistence and verification helpers.

Phase 3 keeps the kickoff contract provider-neutral: front doors and Tower see
runtime truth fields and kickoff verification state, while provider-specific
prompt classification remains inside provider adapters.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite  # type: ignore[import-not-found]

from atc.orchestration.handoff import (
    HandoffPayloadKind,
    handoff_from_delivery_result,
)
from atc.runtime.models import (
    BlockerReason,
    DeliveryState,
    RecoveryRecommendation,
    RecoveryState,
    RoleKind,
    RuntimeDeliveryResult,
    RuntimeState,
)
from atc.runtime.tracing import new_trace_id
from atc.state import db as db_ops


@dataclass(slots=True)
class LeaderKickoffPayload:
    """Persisted source-of-truth kickoff payload for Leader recovery."""

    project_id: str
    goal: str
    message: str
    source: str
    auto_kickoff: bool = True
    trace_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "goal": self.goal,
            "message": self.message,
            "source": self.source,
            "auto_kickoff": self.auto_kickoff,
            "trace_id": self.trace_id,
        }


@dataclass(slots=True)
class LeaderKickoffVerification:
    """Provider-neutral Leader kickoff verification verdict."""

    kickoff_verified: bool
    kickoff_state: str
    runtime_created: bool
    payload_written: bool
    submit_sent: bool
    provider_accepted: bool
    goal_accepted: bool
    leader_reported_active: bool
    leader_began_work: bool
    startup_handshake_state: str
    goal_acceptance_state: str
    first_actionable_step_observed_at: str | None = None
    task_graph_created_at: str | None = None
    kickoff_blocker_reason: str | None = None
    kickoff_recovery_recommendation: dict[str, Any] | None = None
    delivery_trace_id: str | None = None
    managed_handoff: dict[str, Any] | None = None
    blocker_reason: str | None = None
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kickoff_verified": self.kickoff_verified,
            "kickoff_state": self.kickoff_state,
            "runtime_created": self.runtime_created,
            "payload_written": self.payload_written,
            "submit_sent": self.submit_sent,
            "provider_accepted": self.provider_accepted,
            "goal_accepted": self.goal_accepted,
            "leader_reported_active": self.leader_reported_active,
            "leader_began_work": self.leader_began_work,
            "startup_handshake_state": self.startup_handshake_state,
            "goal_acceptance_state": self.goal_acceptance_state,
            "first_actionable_step_observed_at": self.first_actionable_step_observed_at,
            "task_graph_created_at": self.task_graph_created_at,
            "kickoff_blocker_reason": self.kickoff_blocker_reason,
            "kickoff_recovery_recommendation": self.kickoff_recovery_recommendation,
            "delivery_trace_id": self.delivery_trace_id,
            "managed_handoff": self.managed_handoff,
        }
        if self.blocker_reason:
            data["blocker_reason"] = self.blocker_reason
        if self.message:
            data["message"] = self.message
        return data


def build_leader_kickoff_message(
    *,
    project_id: str,
    project_name: str,
    goal: str,
    description: str | None = None,
    repo_path: str | None = None,
    github_repo: str | None = None,
    context_rows: list[tuple[str, str]] | None = None,
    api_style: str = "compact",
) -> str:
    """Build the Leader mission brief used by API/Tower kickoff paths."""

    lines = [
        f"# Mission Brief — {project_name}",
        "",
        "## Goal",
        goal,
        "",
    ]
    if description:
        lines += ["## Project Description", description, ""]
    if repo_path:
        lines += ["## Repository", f"Local path: {repo_path}", ""]
    if github_repo:
        lines += [f"GitHub: {github_repo}", ""]
    if context_rows:
        lines += ["## Project Context", ""]
        for key, val in context_rows:
            if key not in {"goal", "project_description", "repo_path", "github_repo"}:
                lines += [f"**{key}:** {val}", ""]
    lines += ["## Your Instructions"]
    if api_style == "explicit-api":
        lines += [
            "You are the project Leader, not the implementer.",
            "Do NOT write product files yourself.",
            "Operate through the ATC orchestration API only.",
            "",
            "1. First decompose the goal into well-scoped tasks via POST "
            f"/api/projects/{project_id}/leader/decompose.",
            "2. Then spawn Aces for ready tasks via POST "
            f"/api/projects/{project_id}/leader/spawn-aces.",
            f"3. Then instruct spawned Aces via POST /api/projects/{project_id}/leader/instruct.",
            f"4. Monitor progress via GET /api/projects/{project_id}/leader/progress.",
            "5. Drive the project to completion by delegating, not by coding directly.",
            "6. When verified done, report completion via POST "
            f"/api/projects/{project_id}/leader/report-complete.",
            "",
            f"Project ID: {project_id}",
            "Before decomposing or starting task work, report goal acceptance via POST "
            f"/api/projects/{project_id}/leader/report-active with JSON "
            "{\"goal_accepted\": true}. This active report is required proof that you "
            "accepted the kickoff.",
            "Begin NOW by reporting active, then decomposing; do not explore the workspace first.",
        ]
    else:
        lines += [
            "1. Decompose the goal into well-scoped tasks using the API.",
            "2. Spawn Aces for each ready task.",
            "3. Monitor progress and drive to completion.",
            "4. When all work is verified, call the Leader completion hook; do not wait",
            "   for Tower to poll.",
            "",
            "Before task work, report goal acceptance through the ATC Leader active-report API.",
            "Complete handoff by reporting done through the ATC Leader completion API.",
            "Begin NOW. Do not ask for clarification — report active, then start decomposing.",
        ]
    return "\n".join(lines)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _leader_context_dict(leader: Any) -> dict[str, Any]:
    if isinstance(leader.context, dict):
        return dict(leader.context)
    if leader.context:
        try:
            parsed = json.loads(leader.context)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def report_leader_goal_accepted(
    conn: aiosqlite.Connection,
    *,
    project_id: str,
    goal_accepted: bool = True,
    message: str | None = None,
) -> dict[str, Any]:
    """Persist the canonical Leader-originated active/goal-accepted report."""

    leader = await db_ops.get_leader_by_project(conn, project_id)
    if leader is None:
        raise ValueError(f"No leader found for project {project_id}")
    context = _leader_context_dict(leader)
    context.setdefault("project_id", project_id)
    if leader.goal:
        context.setdefault("goal", leader.goal)
    reported_at = _now()
    report = {
        "leader_reported_active": True,
        "goal_accepted": bool(goal_accepted),
        "reported_at": reported_at,
        "message": message,
    }
    context["leader_active_report"] = report
    existing_handoff = context.get("managed_handoff")
    if isinstance(existing_handoff, dict):
        existing_handoff["child_reported_active"] = True
        existing_handoff["lifecycle_state"] = "child_reported_active"
        existing_handoff["handoff_verified"] = False
        context["managed_handoff"] = existing_handoff
    await conn.execute(
        (
            "UPDATE leaders SET context = ?, status = 'managing', "
            "updated_at = datetime('now') WHERE id = ?"
        ),
        (json.dumps(context), leader.id),
    )
    await conn.commit()
    return report


async def persist_leader_kickoff_payload(
    conn: aiosqlite.Connection,
    *,
    project_id: str,
    goal: str,
    message: str,
    source: str,
    auto_kickoff: bool = True,
    trace_id: str | None = None,
) -> LeaderKickoffPayload:
    """Persist original kickoff payload on the Leader context for deterministic recovery."""

    leader = await db_ops.get_leader_by_project(conn, project_id)
    if leader is None:
        raise ValueError(f"No leader found for project {project_id}")
    context: dict[str, Any]
    if isinstance(leader.context, dict):
        context = dict(leader.context)
    elif leader.context:
        try:
            parsed = json.loads(leader.context)
            context = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            context = {}
    else:
        context = {}
    payload = LeaderKickoffPayload(
        project_id=project_id,
        goal=goal,
        message=message,
        source=source,
        auto_kickoff=auto_kickoff,
        trace_id=trace_id or new_trace_id(),
    )
    context["leader_kickoff_payload"] = payload.as_dict()
    context["leader_original_goal"] = goal
    context["managed_handoff"] = {
        "parent_role": RoleKind.TOWER.value,
        "child_role": RoleKind.LEADER.value,
        "payload_kind": HandoffPayloadKind.LEADER_GOAL.value,
        "lifecycle_state": "session_created",
        "project_id": project_id,
        "payload_hash": payload.trace_id,
        "trace_id": payload.trace_id,
        "handoff_verified": False,
        "child_reported_active": False,
    }
    await conn.execute(
        "UPDATE leaders SET context = ?, goal = ?, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(context), goal, leader.id),
    )
    await conn.commit()
    return payload


def _startup_handshake_state(
    runtime_state: RuntimeState | None,
    delivery_state: DeliveryState | None,
    blocker: BlockerReason | None,
) -> str:
    """Return provider-neutral startup readiness for kickoff response fields."""

    if blocker is not None or delivery_state is DeliveryState.BLOCKED:
        return "blocked"
    if runtime_state in {RuntimeState.FAILED, RuntimeState.MISSING, RuntimeState.STALE}:
        return "failed"
    if runtime_state in {
        RuntimeState.READY,
        RuntimeState.ACTIVE,
        RuntimeState.IDLE,
        RuntimeState.IDLE_AT_DEFAULT_PROMPT,
    }:
        return "ready"
    if delivery_state in {
        DeliveryState.RUNTIME_CREATED,
        DeliveryState.PROMPT_VISIBLE,
        DeliveryState.PAYLOAD_WRITTEN,
        DeliveryState.SUBMIT_SENT,
        DeliveryState.SUBMITTED_PENDING_ACCEPTANCE,
        DeliveryState.ACCEPTED_ACTIVE,
    }:
        return "runtime_created"
    return "not_started"


def _goal_acceptance_state(
    *,
    provider_accepted: bool,
    goal_accepted: bool,
    leader_reported_active: bool,
    leader_began_work: bool,
    submit_sent: bool,
    blocker: BlockerReason | None,
    failed: bool,
) -> str:
    """Return provider-neutral goal acceptance without optimistic wording."""

    if blocker is not None:
        return "blocked"
    if failed:
        return "failed"
    if goal_accepted and leader_reported_active and leader_began_work:
        return "leader_reported_active"
    if goal_accepted and leader_reported_active:
        return "goal_accepted"
    if provider_accepted:
        return "provider_active_unverified"
    if submit_sent:
        return "submitted_pending_acceptance"
    return "not_submitted"


def _recovery_recommendation(
    blocker: BlockerReason | None,
) -> dict[str, Any] | None:
    if blocker is None:
        return None
    return RecoveryRecommendation(
        state=RecoveryState.BLOCKED,
        command="atc leader recover --project-id <project-id> --dry-run",
        safety="inspect_first",
        message="Inspect Leader runtime health before resending the persisted kickoff payload.",
        requires_operator=blocker
        not in {BlockerReason.PROMPT_NOT_SUBMITTED, BlockerReason.DELIVERY_UNVERIFIED},
    ).as_dict()


def verify_leader_kickoff_delivery(
    result: RuntimeDeliveryResult | None,
    *,
    leader_reported_active: bool = False,
    goal_accepted: bool = False,
    first_actionable_step_observed_at: str | None = None,
    task_graph_created_at: str | None = None,
) -> LeaderKickoffVerification:
    """Classify a kickoff delivery result into explicit startup guarantees."""

    if result is None:
        handoff = handoff_from_delivery_result(
            None,
            parent_role=RoleKind.TOWER,
            child_role=RoleKind.LEADER,
            payload_kind=HandoffPayloadKind.LEADER_GOAL,
        )
        return LeaderKickoffVerification(
            kickoff_verified=False,
            kickoff_state="queued_unverified",
            runtime_created=False,
            payload_written=False,
            submit_sent=False,
            provider_accepted=False,
            goal_accepted=False,
            leader_reported_active=False,
            leader_began_work=False,
            startup_handshake_state="not_started",
            goal_acceptance_state="not_submitted",
            managed_handoff=handoff.as_dict(),
            message="Kickoff delivery was queued but not observed",
        )

    delivery = result.delivery_state
    runtime_state = result.runtime_state
    blocker = result.blocker_reason
    runtime_created = delivery in {
        DeliveryState.RUNTIME_CREATED,
        DeliveryState.PROMPT_VISIBLE,
        DeliveryState.PAYLOAD_WRITTEN,
        DeliveryState.SUBMIT_SENT,
        DeliveryState.SUBMITTED_PENDING_ACCEPTANCE,
        DeliveryState.ACCEPTED_ACTIVE,
        DeliveryState.BLOCKED,
    } or runtime_state in {
        RuntimeState.READY,
        RuntimeState.ACTIVE,
        RuntimeState.BLOCKED,
        RuntimeState.IDLE,
        RuntimeState.IDLE_AT_DEFAULT_PROMPT,
    }
    payload_written = delivery in {
        DeliveryState.PAYLOAD_WRITTEN,
        DeliveryState.SUBMIT_SENT,
        DeliveryState.SUBMITTED_PENDING_ACCEPTANCE,
        DeliveryState.ACCEPTED_ACTIVE,
    }
    submit_sent = delivery in {
        DeliveryState.SUBMIT_SENT,
        DeliveryState.SUBMITTED_PENDING_ACCEPTANCE,
        DeliveryState.ACCEPTED_ACTIVE,
    }
    provider_accepted = delivery is DeliveryState.ACCEPTED_ACTIVE or result.status in {
        "confirmed",
        "delivered",
    }
    canonical_work_observed = bool(
        first_actionable_step_observed_at or task_graph_created_at
    )
    leader_began_work = bool(leader_reported_active and goal_accepted and canonical_work_observed)
    kickoff_verified = bool(result.ok and submit_sent and leader_began_work)
    failed = result.status == "failed" or delivery is DeliveryState.FAILED

    if blocker is not None or result.status in {"blocked", "failed"}:
        state = "blocked" if result.status == "blocked" else "failed"
    elif kickoff_verified:
        state = "leader_reported_active"
    elif submit_sent:
        state = "submitted_pending_acceptance"
    elif payload_written:
        state = "payload_written"
    elif delivery is DeliveryState.PROMPT_VISIBLE:
        state = "prompt_visible"
    elif delivery is DeliveryState.RUNTIME_CREATED:
        state = "runtime_created"
    else:
        state = "queued_unverified"
    handoff = handoff_from_delivery_result(
        result,
        parent_role=RoleKind.TOWER,
        child_role=RoleKind.LEADER,
        payload_kind=HandoffPayloadKind.LEADER_GOAL,
        child_reported_active=bool(leader_reported_active and goal_accepted),
        first_actionable_step_observed=canonical_work_observed,
        verified_at=first_actionable_step_observed_at or task_graph_created_at,
        recovery_recommendation=(
            result.recovery_recommendation.as_dict()
            if result.recovery_recommendation
            else _recovery_recommendation(blocker)
        ),
    )

    return LeaderKickoffVerification(
        kickoff_verified=kickoff_verified,
        kickoff_state=state,
        runtime_created=runtime_created,
        payload_written=payload_written,
        submit_sent=submit_sent,
        provider_accepted=provider_accepted,
        goal_accepted=goal_accepted,
        leader_reported_active=leader_reported_active,
        leader_began_work=leader_began_work,
        startup_handshake_state=_startup_handshake_state(runtime_state, delivery, blocker),
        goal_acceptance_state=_goal_acceptance_state(
            provider_accepted=provider_accepted,
            goal_accepted=goal_accepted,
            leader_reported_active=leader_reported_active,
            leader_began_work=leader_began_work,
            submit_sent=submit_sent,
            blocker=blocker,
            failed=failed,
        ),
        first_actionable_step_observed_at=first_actionable_step_observed_at,
        task_graph_created_at=task_graph_created_at,
        kickoff_blocker_reason=blocker.value if isinstance(blocker, BlockerReason) else None,
        kickoff_recovery_recommendation=(
            result.recovery_recommendation.as_dict()
            if result.recovery_recommendation
            else _recovery_recommendation(blocker)
        ),
        delivery_trace_id=result.trace_id,
        managed_handoff=handoff.as_dict(),
        blocker_reason=blocker.value if isinstance(blocker, BlockerReason) else None,
        message=result.message,
    )

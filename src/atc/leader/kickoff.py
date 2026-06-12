"""Leader kickoff payload persistence and verification helpers.

Phase 3 keeps the kickoff contract provider-neutral: front doors and Tower see
runtime truth fields and kickoff verification state, while provider-specific
prompt classification remains inside provider adapters.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite  # type: ignore[import-not-found]

from atc.runtime.models import BlockerReason, DeliveryState, RuntimeDeliveryResult, RuntimeState
from atc.state import db as db_ops


@dataclass(slots=True)
class LeaderKickoffPayload:
    """Persisted source-of-truth kickoff payload for Leader recovery."""

    project_id: str
    goal: str
    message: str
    source: str
    auto_kickoff: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "goal": self.goal,
            "message": self.message,
            "source": self.source,
            "auto_kickoff": self.auto_kickoff,
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
    leader_began_work: bool
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
            "leader_began_work": self.leader_began_work,
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
            "3. Then instruct spawned Aces via POST "
            f"/api/projects/{project_id}/leader/instruct.",
            f"4. Monitor progress via GET /api/projects/{project_id}/leader/progress.",
            "5. Drive the project to completion by delegating, not by coding directly.",
            "",
            f"Project ID: {project_id}",
            "Begin NOW by decomposing, not by exploring the workspace.",
        ]
    else:
        lines += [
            "1. Decompose the goal into well-scoped tasks using the API.",
            "2. Spawn Aces for each ready task.",
            "3. Monitor progress and drive to completion.",
            "4. Report back to Tower when done.",
            "",
            "Begin NOW. Do not ask for clarification — start decomposing.",
        ]
    return "\n".join(lines)


async def persist_leader_kickoff_payload(
    conn: aiosqlite.Connection,
    *,
    project_id: str,
    goal: str,
    message: str,
    source: str,
    auto_kickoff: bool = True,
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
    )
    context["leader_kickoff_payload"] = payload.as_dict()
    context["leader_original_goal"] = goal
    await conn.execute(
        "UPDATE leaders SET context = ?, goal = ?, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(context), goal, leader.id),
    )
    await conn.commit()
    return payload


def verify_leader_kickoff_delivery(
    result: RuntimeDeliveryResult | None,
) -> LeaderKickoffVerification:
    """Classify a kickoff delivery result into explicit startup guarantees."""

    if result is None:
        return LeaderKickoffVerification(
            kickoff_verified=False,
            kickoff_state="queued_unverified",
            runtime_created=False,
            payload_written=False,
            submit_sent=False,
            provider_accepted=False,
            leader_began_work=False,
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
    leader_began_work = (
        runtime_state is RuntimeState.ACTIVE
        or delivery is DeliveryState.ACCEPTED_ACTIVE
    )
    kickoff_verified = bool(result.ok and provider_accepted and leader_began_work)

    if blocker is not None or result.status in {"blocked", "failed"}:
        state = "blocked" if result.status == "blocked" else "failed"
    elif kickoff_verified:
        state = "accepted_active"
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

    return LeaderKickoffVerification(
        kickoff_verified=kickoff_verified,
        kickoff_state=state,
        runtime_created=runtime_created,
        payload_written=payload_written,
        submit_sent=submit_sent,
        provider_accepted=provider_accepted,
        leader_began_work=leader_began_work,
        blocker_reason=blocker.value if isinstance(blocker, BlockerReason) else None,
        message=result.message,
    )

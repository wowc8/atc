"""Provider-neutral runtime health and inspect-first recovery planning."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from atc.runtime.models import (
    BlockerReason,
    DeliveryState,
    ReadinessState,
    RecoveryRecommendation,
    RecoveryState,
    RuntimeInspection,
    RuntimeState,
)
from atc.runtime.service import RuntimeService
from atc.state import db as db_ops

if TYPE_CHECKING:
    import aiosqlite  # type: ignore[import-not-found]

RecoveryMode = Literal["dry_run", "apply"]
RuntimeRole = Literal["leader", "ace"]

_ACTIVE_SESSION_STATUSES = {"connecting", "idle", "working", "waiting", "paused"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class RuntimeHealth:
    """Provider-neutral health summary for a Leader or Ace runtime."""

    role: RuntimeRole
    project_id: str
    runtime_exists: bool
    pane_attached: bool
    provider: str | None
    session_id: str | None = None
    runtime_state: str = RuntimeState.MISSING.value
    delivery_state: str = DeliveryState.NOT_STARTED.value
    blocker_reason: str | None = None
    last_activity_at: str | None = None
    last_inspected_at: str = field(default_factory=_now)
    task_graph_state: dict[str, Any] = field(default_factory=dict)
    kickoff_state: dict[str, Any] = field(default_factory=dict)
    ace_dispatch: dict[str, Any] = field(default_factory=dict)
    ace_count: int = 0
    current_blocker: str | None = None
    recovery_recommendation: dict[str, Any] | None = None
    provider_diagnostics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RecoveryPlan:
    """Inspect-first recovery response. Mutating recovery is policy-gated later."""

    role: RuntimeRole
    project_id: str
    mode: RecoveryMode
    safe_to_apply: bool
    actions: list[dict[str, Any]]
    health: dict[str, Any]
    message: str
    refused_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _blocker_from_inspection(inspection: RuntimeInspection) -> str | None:
    if inspection.readiness is ReadinessState.BLOCKED:
        reason = inspection.block_reason.value if inspection.block_reason else "runtime_blocked"
        mapping = {
            "auth": BlockerReason.RUNTIME_AUTH_REQUIRED.value,
            "login": BlockerReason.RUNTIME_AUTH_REQUIRED.value,
            "trust": BlockerReason.RUNTIME_TRUST_REQUIRED.value,
            "permission": BlockerReason.RUNTIME_PERMISSION_REQUIRED.value,
            "provider_prompt": BlockerReason.UNKNOWN_PROMPT_BLOCKER.value,
            "unknown": BlockerReason.UNKNOWN_PROMPT_BLOCKER.value,
        }
        return mapping.get(reason, reason)
    if inspection.readiness is ReadinessState.ERROR:
        return BlockerReason.PROVIDER_ERROR.value
    if not inspection.alive:
        return BlockerReason.PANE_MISSING.value
    return None


def _runtime_state_from_inspection(inspection: RuntimeInspection) -> str:
    if not inspection.alive:
        return RuntimeState.MISSING.value
    if inspection.readiness is ReadinessState.BLOCKED:
        return RuntimeState.BLOCKED.value
    if inspection.readiness is ReadinessState.ERROR:
        return RuntimeState.FAILED.value
    if inspection.readiness is ReadinessState.BUSY:
        return RuntimeState.ACTIVE.value
    if inspection.readiness is ReadinessState.READY:
        return RuntimeState.READY.value
    if inspection.readiness is ReadinessState.STOPPED:
        return RuntimeState.STALE.value
    return RuntimeState.STARTING.value


def _delivery_state_for_runtime(runtime_state: str, *, has_payload: bool = False) -> str:
    if runtime_state == RuntimeState.ACTIVE.value:
        return DeliveryState.ACCEPTED_ACTIVE.value
    if runtime_state == RuntimeState.BLOCKED.value:
        return DeliveryState.BLOCKED.value
    if runtime_state in {
        RuntimeState.MISSING.value,
        RuntimeState.FAILED.value,
        RuntimeState.STALE.value,
    }:
        return DeliveryState.FAILED.value
    if has_payload:
        return DeliveryState.SUBMITTED_PENDING_ACCEPTANCE.value
    return DeliveryState.NOT_STARTED.value


def _recovery_for(
    role: RuntimeRole, project_id: str, blocker: str | None, session_id: str | None
) -> dict[str, Any]:
    if role == "leader":
        command = f"atc leader recover --project-id {project_id} --dry-run"
    else:
        command = (
            f"atc ace recover --project-id {project_id} "
            f"--ace-id {session_id or '<ace-id>'} --dry-run"
        )
    state = RecoveryState.BLOCKED if blocker else RecoveryState.NOT_NEEDED
    if blocker == BlockerReason.PANE_MISSING.value:
        state = RecoveryState.RESTART_REQUIRED
    return RecoveryRecommendation(
        state=state,
        command=command,
        safety="inspect_first",
        message=(
            "Inspect runtime truth before attempting recovery. Apply mode is "
            "limited to safe, classified states."
            if blocker
            else "No recovery is currently required."
        ),
        requires_operator=blocker not in {None, BlockerReason.PANE_MISSING.value},
    ).as_dict()


async def _inspect_session(
    session: Any | None,
    runtime_service: RuntimeService,
) -> tuple[str, str | None, dict[str, Any]]:
    if session is None:
        return RuntimeState.MISSING.value, BlockerReason.PANE_MISSING.value, {}
    if not getattr(session, "tmux_pane", None):
        return (
            RuntimeState.MISSING.value,
            BlockerReason.PANE_MISSING.value,
            {
                "status": getattr(session, "status", None),
                "reason": "no_tmux_pane",
            },
        )
    try:
        inspection = await runtime_service.inspect_session_record(session)
    except Exception as exc:
        return (
            RuntimeState.FAILED.value,
            BlockerReason.PROVIDER_ERROR.value,
            {
                "status": getattr(session, "status", None),
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
    runtime_state = _runtime_state_from_inspection(inspection)
    blocker = _blocker_from_inspection(inspection)
    diagnostics = {
        "status": getattr(session, "status", None),
        "readiness": inspection.readiness.value,
        "alive": inspection.alive,
        "summary": inspection.summary,
        "details": inspection.details,
    }
    if inspection.last_output_excerpt:
        diagnostics["redacted_excerpt"] = inspection.last_output_excerpt
    return runtime_state, blocker, diagnostics


async def leader_health(
    conn: aiosqlite.Connection,
    project_id: str,
    *,
    runtime_service: RuntimeService | None = None,
) -> RuntimeHealth:
    """Build provider-neutral Leader health for a project."""

    service = runtime_service or RuntimeService()
    leader = await db_ops.get_leader_by_project(conn, project_id)
    session = (
        await db_ops.get_session(conn, leader.session_id) if leader and leader.session_id else None
    )
    runtime_state, blocker, diagnostics = await _inspect_session(session, service)
    tasks = await db_ops.list_task_graphs(conn, project_id=project_id)
    aces = await db_ops.list_sessions(conn, project_id=project_id, session_type="ace")
    assignments = await db_ops.list_task_assignments(conn)
    project_assignments = [
        assignment
        for assignment in assignments
        if any(task.id == assignment.task_graph_id for task in tasks)
    ]
    latest_activity = max(
        [
            value
            for value in [
                getattr(session, "updated_at", None),
                *(a.last_activity_at for a in project_assignments),
            ]
            if value
        ],
        default=None,
    )
    context = leader.context if leader and isinstance(leader.context, dict) else {}
    kickoff_payload = context.get("leader_kickoff_payload") if isinstance(context, dict) else None
    kickoff_state = {
        "kickoff_payload_persisted": bool(kickoff_payload),
        "kickoff_state": (
            DeliveryState.ACCEPTED_ACTIVE.value
            if runtime_state == RuntimeState.ACTIVE.value
            else _delivery_state_for_runtime(runtime_state, has_payload=bool(kickoff_payload))
        ),
        "original_goal_available": bool(
            context.get("leader_original_goal") or (leader.goal if leader else None)
        ),
    }
    task_summary = {
        "total": len(tasks),
        "todo": sum(1 for task in tasks if task.status == "todo"),
        "assigned": sum(1 for task in tasks if task.status == "assigned"),
        "in_progress": sum(1 for task in tasks if task.status == "in_progress"),
        "done": sum(1 for task in tasks if task.status == "done"),
        "failed": sum(1 for task in tasks if task.status == "failed"),
    }
    dispatch_summary = {
        "total": len(project_assignments),
        "verified": sum(1 for a in project_assignments if a.dispatch_verified),
        "blocked": sum(1 for a in project_assignments if a.blocker_reason),
        "unverified": sum(1 for a in project_assignments if not a.dispatch_verified),
    }
    current_blocker = blocker or next(
        (a.blocker_reason for a in project_assignments if a.blocker_reason), None
    )
    return RuntimeHealth(
        role="leader",
        project_id=project_id,
        session_id=session.id if session else None,
        runtime_exists=session is not None and session.status in _ACTIVE_SESSION_STATUSES,
        pane_attached=bool(getattr(session, "tmux_pane", None)),
        provider=getattr(session, "provider", None),
        runtime_state=runtime_state,
        delivery_state=_delivery_state_for_runtime(
            runtime_state, has_payload=bool(kickoff_payload)
        ),
        blocker_reason=blocker,
        last_activity_at=latest_activity,
        task_graph_state=task_summary,
        kickoff_state=kickoff_state,
        ace_dispatch=dispatch_summary,
        ace_count=len(aces),
        current_blocker=current_blocker,
        recovery_recommendation=_recovery_for(
            "leader", project_id, current_blocker, session.id if session else None
        ),
        provider_diagnostics=diagnostics,
    )


async def ace_health(
    conn: aiosqlite.Connection,
    project_id: str,
    ace_id: str,
    *,
    runtime_service: RuntimeService | None = None,
) -> RuntimeHealth:
    """Build provider-neutral Ace health for one Ace session."""

    service = runtime_service or RuntimeService()
    session = await db_ops.get_session(conn, ace_id)
    if session is None or session.project_id != project_id or session.session_type != "ace":
        runtime_state, blocker, diagnostics = await _inspect_session(None, service)
        return RuntimeHealth(
            role="ace",
            project_id=project_id,
            session_id=ace_id,
            runtime_exists=False,
            pane_attached=False,
            provider=None,
            runtime_state=runtime_state,
            delivery_state=DeliveryState.NOT_STARTED.value,
            blocker_reason=blocker,
            task_graph_state={},
            ace_dispatch={},
            ace_count=0,
            current_blocker=blocker,
            recovery_recommendation=_recovery_for("ace", project_id, blocker, ace_id),
            provider_diagnostics=diagnostics,
        )
    runtime_state, blocker, diagnostics = await _inspect_session(session, service)
    assignments = await db_ops.list_task_assignments(conn, ace_session_id=ace_id)
    active_assignment = assignments[0] if assignments else None
    task = (
        await db_ops.get_task_graph(conn, active_assignment.task_graph_id)
        if active_assignment is not None
        else None
    )
    assignment_blocker = active_assignment.blocker_reason if active_assignment else None
    current_blocker = blocker or assignment_blocker
    ace_dispatch = {
        "assignment_id": active_assignment.assignment_id if active_assignment else None,
        "task_graph_id": active_assignment.task_graph_id if active_assignment else None,
        "assignment_status": active_assignment.status if active_assignment else None,
        "dispatch_delivery_state": active_assignment.dispatch_delivery_state
        if active_assignment
        else DeliveryState.NOT_STARTED.value,
        "dispatch_verified": active_assignment.dispatch_verified if active_assignment else False,
        "assigned_task_id": active_assignment.assigned_task_id if active_assignment else None,
        "blocker_reason": assignment_blocker,
    }
    task_state = {
        "task_graph_id": task.id if task else None,
        "status": task.status if task else None,
        "assigned_ace_id": task.assigned_ace_id if task else None,
    }
    delivery_state = (
        active_assignment.dispatch_delivery_state
        if active_assignment is not None
        else _delivery_state_for_runtime(runtime_state)
    )
    return RuntimeHealth(
        role="ace",
        project_id=project_id,
        session_id=ace_id,
        runtime_exists=session is not None and session.status in _ACTIVE_SESSION_STATUSES,
        pane_attached=bool(getattr(session, "tmux_pane", None)),
        provider=getattr(session, "provider", None),
        runtime_state=runtime_state,
        delivery_state=delivery_state,
        blocker_reason=blocker,
        last_activity_at=(
            active_assignment.last_activity_at
            if active_assignment
            else getattr(session, "updated_at", None)
        ),
        task_graph_state=task_state,
        ace_dispatch=ace_dispatch,
        ace_count=1 if session is not None else 0,
        current_blocker=current_blocker,
        recovery_recommendation=_recovery_for("ace", project_id, current_blocker, ace_id),
        provider_diagnostics=diagnostics,
    )


def build_recovery_plan(
    health: RuntimeHealth,
    *,
    mode: RecoveryMode = "dry_run",
    policy: str = "inspect_first",
) -> RecoveryPlan:
    """Return an inspect-first recovery plan without provider-specific mechanics."""

    actions: list[dict[str, Any]] = [
        {
            "action": "inspect_runtime",
            "status": "complete",
            "runtime_state": health.runtime_state,
            "blocker_reason": health.current_blocker,
        }
    ]
    if not health.current_blocker and health.runtime_state in {
        RuntimeState.READY.value,
        RuntimeState.ACTIVE.value,
    }:
        actions.append({"action": "none", "reason": "runtime_healthy"})
        safe = False
        message = "Runtime appears healthy; no recovery action planned."
    elif health.current_blocker == BlockerReason.PANE_MISSING.value:
        actions.append({"action": "restart_required", "safe": True})
        safe = True
        message = (
            "Runtime pane is missing; restart/re-dispatch can be planned from persisted state."
        )
    else:
        actions.append({"action": "operator_intervention_required", "safe": False})
        safe = False
        message = (
            "Recovery requires operator/provider policy; apply refused by inspect-first contract."
        )

    refused = None
    if mode == "apply" and not safe:
        refused = "unsafe_or_unneeded_recovery"
    elif mode == "apply" and policy == "inspect_first":
        refused = "apply_requires_explicit_policy"

    if refused:
        message = f"Apply refused: {refused}. Re-run as dry-run or choose an explicit safe policy."

    return RecoveryPlan(
        role=health.role,
        project_id=health.project_id,
        mode=mode,
        safe_to_apply=safe,
        actions=actions,
        health=health.as_dict(),
        message=message,
        refused_reason=refused,
    )

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
    provider_blocker = inspection.details.get("blocker_reason")
    if isinstance(provider_blocker, str):
        valid_blockers = {reason.value for reason in BlockerReason}
        if provider_blocker in valid_blockers:
            return provider_blocker
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


def _leader_kickoff_health_state(
    *,
    runtime_state: str,
    blocker: str | None,
    has_payload: bool,
    leader_reported_active: bool,
    goal_accepted: bool,
    task_total: int,
    first_actionable_step_observed_at: str | None,
) -> str:
    """Return Phase 3 provider-neutral Leader startup/working state."""

    if blocker == BlockerReason.PANE_MISSING.value:
        return "runtime_missing"
    if blocker:
        return "blocked_on_provider_prompt"
    if runtime_state in {RuntimeState.MISSING.value, RuntimeState.STARTING.value}:
        return "starting"
    if runtime_state == RuntimeState.FAILED.value:
        return "failed"
    if not has_payload:
        return "starting"
    if not (leader_reported_active and goal_accepted):
        return "kickoff_unverified"
    if task_total <= 0 and not first_actionable_step_observed_at:
        return "task_graph_empty"
    return "working"


def _provider_details_from_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    details = diagnostics.get("details")
    return details if isinstance(details, dict) else {}


def _pending_prompt_text_from_diagnostics(diagnostics: dict[str, Any]) -> str | None:
    details = _provider_details_from_diagnostics(diagnostics)
    provider_diagnostics = details.get("provider_diagnostics")
    if not isinstance(provider_diagnostics, dict):
        return None
    text = provider_diagnostics.get("pending_prompt_text")
    return text if isinstance(text, str) and text.strip() else None


def _pending_prompt_matches_payload(
    pending_prompt_text: str | None,
    persisted_payload: dict[str, Any] | None,
) -> bool:
    if not pending_prompt_text or not isinstance(persisted_payload, dict):
        return False
    message = persisted_payload.get("message")
    if not isinstance(message, str) or not message.strip():
        return False
    pending = " ".join(pending_prompt_text.split())
    expected = " ".join(message.split())
    if not pending:
        return False
    if pending == expected:
        return True
    return len(pending) >= 12 and pending in expected


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
    active_report = context.get("leader_active_report") if isinstance(context, dict) else None
    leader_reported_active = bool(
        isinstance(active_report, dict) and active_report.get("leader_reported_active")
    )
    goal_accepted = bool(isinstance(active_report, dict) and active_report.get("goal_accepted"))
    leader_active_reported_at = (
        active_report.get("reported_at") if isinstance(active_report, dict) else None
    )
    task_graph_created_at = min(
        [task.created_at for task in tasks if task.created_at],
        default=None,
    )
    first_actionable_step_observed_at = task_graph_created_at
    kickoff_trace_id = (
        kickoff_payload.get("trace_id") if isinstance(kickoff_payload, dict) else None
    )
    pending_prompt_text = _pending_prompt_text_from_diagnostics(diagnostics)
    pending_payload_matches = _pending_prompt_matches_payload(
        pending_prompt_text,
        kickoff_payload if isinstance(kickoff_payload, dict) else None,
    )
    kickoff_state = {
        "kickoff_payload_persisted": bool(kickoff_payload),
        "kickoff_state": _leader_kickoff_health_state(
            runtime_state=runtime_state,
            blocker=blocker,
            has_payload=bool(kickoff_payload),
            leader_reported_active=leader_reported_active,
            goal_accepted=goal_accepted,
            task_total=len(tasks),
            first_actionable_step_observed_at=first_actionable_step_observed_at,
        ),
        "startup_handshake_state": "blocked"
        if blocker
        else (
            "ready"
            if runtime_state in {RuntimeState.READY.value, RuntimeState.ACTIVE.value}
            else "unknown"
        ),
        "goal_acceptance_state": (
            "leader_reported_active"
            if leader_reported_active and goal_accepted and first_actionable_step_observed_at
            else (
                "goal_accepted"
                if leader_reported_active and goal_accepted
                else ("submitted_pending_acceptance" if kickoff_payload else "not_submitted")
            )
        ),
        "kickoff_verified": bool(
            not blocker
            and runtime_state in {RuntimeState.READY.value, RuntimeState.ACTIVE.value}
            and leader_reported_active
            and goal_accepted
            and first_actionable_step_observed_at
        ),
        "goal_accepted": goal_accepted,
        "leader_reported_active": leader_reported_active,
        "leader_active_reported_at": leader_active_reported_at,
        "delivery_trace_id": kickoff_trace_id,
        "kickoff_blocker_reason": blocker,
        "first_actionable_step_observed_at": first_actionable_step_observed_at,
        "task_graph_created_at": task_graph_created_at,
        "original_goal_available": bool(
            context.get("leader_original_goal") or (leader.goal if leader else None)
        ),
        "pending_prompt_observed": bool(pending_prompt_text),
        "pending_prompt_matches_persisted_payload": pending_payload_matches,
        "pending_prompt_match_basis": "provider_pending_text"
        if pending_prompt_text
        else None,
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


def _provider_capabilities(health: RuntimeHealth) -> dict[str, Any]:
    details = health.provider_diagnostics.get("details")
    if isinstance(details, dict):
        capabilities = details.get("recovery_capabilities")
        if isinstance(capabilities, dict):
            return capabilities
    return {}


def _restart_policy_required(health: RuntimeHealth) -> str:
    if health.current_blocker == BlockerReason.STALE_AFTER_UPDATE.value:
        return "restart_stale_runtime"
    if health.runtime_state == RuntimeState.STALE.value:
        return "restart_stale_runtime"
    return "restart_missing_pane"


def _recovery_policy_allows(
    policy: str,
    action: str,
    *,
    required_policy: str | None = None,
) -> bool:
    if policy in {"block_only", "notify_operator", "inspect_first"}:
        return False
    if action == "restart":
        return policy == required_policy or (
            policy == "auto_accept_updates_and_restart"
            and required_policy == "restart_stale_runtime"
        )
    return action == "accept_update" and policy == "auto_accept_updates_and_restart"


def _is_stale_runtime_stop_error(exc: Exception) -> bool:
    text = str(exc).lower()
    stale_markers = (
        "no such pane",
        "can't find pane",
        "can't find window",
        "can't find session",
        "pane unavailable",
        "session missing",
        "session not found",
    )
    return isinstance(exc, RuntimeError) and any(marker in text for marker in stale_markers)


def build_recovery_plan(
    health: RuntimeHealth,
    *,
    mode: RecoveryMode = "dry_run",
    policy: str = "inspect_first",
) -> RecoveryPlan:
    """Return provider-policy recovery plan without provider-specific mechanics."""

    capabilities = _provider_capabilities(health)
    actions: list[dict[str, Any]] = [
        {
            "action": "inspect_runtime",
            "status": "complete",
            "runtime_state": health.runtime_state,
            "blocker_reason": health.current_blocker,
        }
    ]
    safe = False
    refused = None
    message = "Runtime appears healthy; no recovery action planned."

    restartable = health.current_blocker in {
        BlockerReason.PANE_MISSING.value,
        BlockerReason.STALE_AFTER_UPDATE.value,
    } or health.runtime_state in {RuntimeState.MISSING.value, RuntimeState.STALE.value}
    update_required = health.current_blocker == BlockerReason.RUNTIME_UPDATE_REQUIRED.value
    pending_prompt = health.current_blocker == BlockerReason.PROMPT_NOT_SUBMITTED.value
    pending_matches_payload = bool(
        health.kickoff_state.get("pending_prompt_matches_persisted_payload")
    )
    required_restart_policy = _restart_policy_required(health) if restartable else None

    if not health.current_blocker and health.runtime_state in {
        RuntimeState.READY.value,
        RuntimeState.ACTIVE.value,
    }:
        actions.append({"action": "none", "reason": "runtime_healthy"})
    elif pending_prompt:
        actions.append(
            {
                "action": "submit_pending_prompt",
                "safe": pending_matches_payload,
                "policy_required": "submit_pending_prompt",
                "uses_persisted_payload": pending_matches_payload,
                "match_basis": health.kickoff_state.get("pending_prompt_match_basis"),
            }
        )
        safe = pending_matches_payload
        if pending_matches_payload:
            message = (
                "Pending provider prompt matches persisted kickoff payload; "
                "Enter-only submit can be applied under explicit policy."
            )
        else:
            message = (
                "Prompt-not-submitted was detected, but the visible prompt did "
                "not match persisted payload."
            )
    elif update_required:
        can_accept = bool(capabilities.get("can_accept_update_prompt"))
        fresh_required = bool(capabilities.get("requires_fresh_session_after_update"))
        actions.append(
            {
                "action": "provider_update_required",
                "safe": can_accept,
                "policy_required": "auto_accept_updates_and_restart",
                "provider_can_accept_update_prompt": can_accept,
                "requires_fresh_session_after_update": fresh_required,
            }
        )
        if can_accept:
            actions.append({"action": "accept_provider_update", "safe": True})
            if fresh_required:
                actions.append({"action": "restart_required", "safe": True})
            safe = True
            message = "Provider update can be accepted under explicit update-and-restart policy."
        else:
            message = (
                "Provider update prompt detected; provider does not advertise safe auto-accept."
            )
    elif restartable:
        actions.append(
            {
                "action": "restart_required",
                "safe": True,
                "policy_required": required_restart_policy,
                "uses_persisted_payload": health.kickoff_state.get("original_goal_available")
                if health.role == "leader"
                else bool(health.ace_dispatch.get("assignment_id")),
            }
        )
        safe = True
        message = "Runtime is stale/missing; restart can be planned from persisted state."
    else:
        actions.append({"action": "operator_intervention_required", "safe": False})
        message = (
            "Recovery requires operator/provider policy; apply refused by inspect-first contract."
        )

    if mode == "apply" and not safe:
        refused = "unsafe_or_unneeded_recovery"
    elif mode == "apply" and pending_prompt and policy != "submit_pending_prompt":
        refused = "apply_requires_submit_pending_prompt_policy"
    elif (
        mode == "apply" and update_required and not _recovery_policy_allows(policy, "accept_update")
    ):
        refused = "apply_requires_update_policy"
    elif (
        mode == "apply"
        and restartable
        and not _recovery_policy_allows(
            policy,
            "restart",
            required_policy=required_restart_policy,
        )
    ):
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


async def apply_recovery_plan(
    conn: aiosqlite.Connection,
    health: RuntimeHealth,
    *,
    policy: str,
    event_bus: Any | None = None,
) -> RecoveryPlan:
    """Apply safe provider-neutral recovery actions.

    Provider-specific update acceptance remains in provider adapters. This first
    mutating slice restarts stale/missing Leader runtimes from persisted kickoff
    state and refuses provider-update prompts unless the provider advertises safe
    update acceptance.
    """

    plan = build_recovery_plan(health, mode="apply", policy=policy)
    if plan.refused_reason:
        return plan

    if health.current_blocker == BlockerReason.PROMPT_NOT_SUBMITTED.value and any(
        action.get("action") == "submit_pending_prompt" for action in plan.actions
    ):
        service = RuntimeService()
        session = await db_ops.get_session(conn, health.session_id) if health.session_id else None
        if session is None:
            plan.refused_reason = "runtime_session_missing"
            plan.message = "Apply refused: runtime session is missing."
            return plan
        inspection = await service.inspect_session_record(session)
        blocker = _blocker_from_inspection(inspection)
        if blocker != BlockerReason.PROMPT_NOT_SUBMITTED.value:
            plan.refused_reason = "runtime_state_changed"
            plan.message = "Apply refused: runtime is no longer prompt_not_submitted."
            plan.actions.append(
                {
                    "action": "reinspect_runtime",
                    "status": "refused",
                    "blocker_reason": blocker,
                }
            )
            return plan
        submitted = await service.submit_pending_prompt_for_session_record(session, inspection)
        if not submitted:
            plan.refused_reason = "provider_refused_pending_prompt_submit"
            plan.message = (
                "Apply refused: provider adapter did not confirm safe pending "
                "prompt submission."
            )
            return plan
        plan.actions.append(
            {"action": "submit_pending_prompt", "status": "applied", "session_id": session.id}
        )
        plan.message = "Pending kickoff prompt submitted via provider adapter."
        plan.health = (
            await leader_health(conn, health.project_id)
            if health.role == "leader"
            else await ace_health(conn, health.project_id, session.id)
        ).as_dict()
        return plan

    if health.current_blocker == BlockerReason.RUNTIME_UPDATE_REQUIRED.value:
        # Phase 6 wires policy/capability semantics. Actual keypress/update
        # mechanics must be provider-owned, so refuse if no provider-owned
        # capability/action has been integrated yet.
        plan.refused_reason = "provider_update_apply_not_implemented"
        plan.message = (
            "Apply refused: provider update handling must be executed by a "
            "provider adapter capability before ATC restarts the runtime."
        )
        return plan

    if health.role == "leader" and any(
        action.get("action") == "restart_required" for action in plan.actions
    ):
        from atc.leader import leader as leader_ops

        leader = await db_ops.get_leader_by_project(conn, health.project_id)
        context = leader.context if leader and isinstance(leader.context, dict) else {}
        goal = context.get("leader_original_goal") or (leader.goal if leader else None) or ""
        if not goal:
            plan.refused_reason = "missing_persisted_goal"
            plan.message = "Apply refused: persisted Leader goal is unavailable."
            return plan
        try:
            await leader_ops.stop_leader(conn, health.project_id, event_bus=event_bus)
        except Exception as exc:
            if not _is_stale_runtime_stop_error(exc):
                plan.refused_reason = "stop_leader_failed"
                plan.message = (
                    "Apply refused: stopping the current Leader failed for a non-stale-pane reason."
                )
                plan.actions.append(
                    {
                        "action": "stop_leader",
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                return plan
            plan.actions.append(
                {
                    "action": "stop_stale_leader",
                    "status": "degraded",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            if leader and leader.session_id:
                await db_ops.update_session_status(
                    conn,
                    leader.session_id,
                    "disconnected",
                )
                await conn.execute(
                    "UPDATE leaders SET session_id = NULL, status = 'idle',"
                    " updated_at = datetime('now') WHERE id = ?",
                    (leader.id,),
                )
                await conn.commit()
        session_id = await leader_ops.start_leader(
            conn, health.project_id, goal=goal, event_bus=event_bus
        )
        plan.actions.append(
            {
                "action": "restart_leader",
                "status": "applied",
                "session_id": session_id,
                "goal_restored": True,
            }
        )
        plan.message = "Leader runtime restarted from persisted goal."
        plan.health = (await leader_health(conn, health.project_id)).as_dict()
        return plan

    if health.role == "ace" and any(
        action.get("action") == "restart_required" for action in plan.actions
    ):
        plan.refused_reason = "leader_owned_ace_recovery_required"
        plan.message = (
            "Apply refused: Ace restart/re-dispatch must be initiated by the "
            "Leader-owned task assignment flow."
        )
        return plan

    return plan

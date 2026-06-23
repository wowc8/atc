"""Provider-neutral role boundary policy for ATC hierarchy operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException, Request

from atc.state import db as db_ops

RoleName = Literal["tower", "leader", "ace", "operator", "unknown"]

TOWER_BLOCKED_ACTIONS = {
    "ace.create",
    "ace.start",
    "ace.stop",
    "ace.message",
    "ace.health",
    "ace.recover",
    "ace.delete",
    "tasks.assign",
}


@dataclass(frozen=True)
class BoundaryDecision:
    allowed: bool
    reason: str
    caller_role: str
    action: str
    target_role: str
    break_glass: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "caller_role": self.caller_role,
            "action": self.action,
            "target_role": self.target_role,
            "break_glass": self.break_glass,
        }


def normalize_role(value: str | None) -> str:
    role = (value or "unknown").strip().lower().replace("_", "-")
    if role in {"tower", "leader", "ace", "operator"}:
        return role
    return "unknown"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def request_boundary_context(request: Request) -> dict[str, str | bool | None]:
    """Extract role-boundary metadata from standard headers/query params."""

    caller_role = request.headers.get("x-atc-caller-role") or request.query_params.get(
        "caller_role"
    )
    break_glass_value = request.headers.get(
        "x-atc-break-glass-approved"
    ) or request.query_params.get("break_glass_approved")
    reason = request.headers.get("x-atc-break-glass-reason") or request.query_params.get(
        "break_glass_reason"
    )
    return {
        "caller_role": normalize_role(caller_role),
        "break_glass_approved": _truthy(break_glass_value),
        "break_glass_reason": reason,
    }


def evaluate_boundary(
    *,
    caller_role: str | None,
    action: str,
    target_role: str,
    break_glass_approved: bool = False,
    break_glass_reason: str | None = None,
) -> BoundaryDecision:
    """Return a provider-neutral allow/block decision for a role-boundary operation."""

    normalized = normalize_role(caller_role)
    if normalized == "tower" and action in TOWER_BLOCKED_ACTIONS:
        if break_glass_approved and (break_glass_reason or "").strip():
            return BoundaryDecision(
                allowed=True,
                reason="tower_break_glass_approved",
                caller_role=normalized,
                action=action,
                target_role=target_role,
                break_glass=True,
            )
        return BoundaryDecision(
            allowed=False,
            reason="tower_must_delegate_ace_operations_to_leader",
            caller_role=normalized,
            action=action,
            target_role=target_role,
        )
    return BoundaryDecision(
        allowed=True,
        reason="allowed",
        caller_role=normalized,
        action=action,
        target_role=target_role,
    )


async def audit_boundary_decision(
    request: Request,
    decision: BoundaryDecision,
    *,
    project_id: str | None = None,
    session_id: str | None = None,
    break_glass_reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist an audit event for blocked or break-glass boundary decisions."""

    if decision.allowed and not decision.break_glass:
        return
    db = getattr(request.app.state, "db", None)
    if db is None:
        return
    detail: dict[str, Any] = {
        **decision.as_dict(),
        "break_glass_reason": break_glass_reason,
    }
    if metadata:
        detail.update(metadata)
    await db_ops.create_app_event(
        db,
        level="warning" if decision.allowed else "error",
        category="role_boundary",
        message=(
            "Role-boundary break-glass approved"
            if decision.break_glass
            else "Role-boundary operation blocked"
        ),
        detail=detail,
        project_id=project_id,
        session_id=session_id,
    )


async def enforce_boundary(
    request: Request,
    *,
    action: str,
    target_role: str,
    project_id: str | None = None,
    session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> BoundaryDecision:
    """Enforce hierarchy boundaries, raising HTTP 403 for blocked operations."""

    context = request_boundary_context(request)
    break_glass_reason = (
        context["break_glass_reason"] if isinstance(context["break_glass_reason"], str) else None
    )
    decision = evaluate_boundary(
        caller_role=str(context["caller_role"]),
        action=action,
        target_role=target_role,
        break_glass_approved=bool(context["break_glass_approved"]),
        break_glass_reason=break_glass_reason,
    )
    await audit_boundary_decision(
        request,
        decision,
        project_id=project_id,
        session_id=session_id,
        break_glass_reason=break_glass_reason,
        metadata=metadata,
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=403,
            detail={
                **decision.as_dict(),
                "recommended_action": "nudge_or_recover_leader_instead_of_managing_ace_directly",
                "break_glass_required": True,
                "break_glass_parameters": {
                    "caller_role": "tower",
                    "break_glass_approved": True,
                    "break_glass_reason": "operator-approved reason",
                },
            },
        )
    return decision

"""Shared API helpers for truthful runtime delivery status responses."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from atc.runtime.models import RuntimeDeliveryResult


class DeliveryStatusResponse(BaseModel):
    """Provider-neutral API response for terminal/runtime delivery attempts.

    ``status`` is the highest guarantee the endpoint can truthfully make:
    - queued: work was scheduled asynchronously and has not been delivered yet
    - submitted: the app attempted a low-level tmux/key delivery but has no provider ack
    - delivered/confirmed: provider/runtime delivery returned positive evidence
    - blocked/failed: delivery did not complete and requires recovery/operator action
    """

    status: str
    delivery_state: str = Field(
        ...,
        description=(
            "Truthful delivery guarantee: "
            "queued|submitted|delivered|confirmed|blocked|failed"
        ),
    )
    message: str | None = None
    session_id: str | None = None
    project_id: str | None = None
    leader_session_id: str | None = None
    provider: str | None = None
    delivery: dict[str, Any] | None = None
    recovery: str | None = None


def delivery_response(
    result: RuntimeDeliveryResult | None,
    *,
    fallback_state: str = "submitted",
    message: str | None = None,
    session_id: str | None = None,
    project_id: str | None = None,
    leader_session_id: str | None = None,
    provider: str | None = None,
    recovery: str | None = None,
) -> dict[str, Any]:
    """Build a truthful response from an optional runtime delivery result."""

    if result is None:
        return DeliveryStatusResponse(
            status=fallback_state,
            delivery_state=fallback_state,
            message=message,
            session_id=session_id,
            project_id=project_id,
            leader_session_id=leader_session_id,
            provider=provider,
            recovery=recovery,
        ).model_dump(exclude_none=True)

    state = "submitted" if result.status == "accepted" else result.status
    return DeliveryStatusResponse(
        status=state,
        delivery_state=state,
        message=result.message or message,
        session_id=result.session_id,
        project_id=project_id,
        leader_session_id=leader_session_id,
        provider=result.provider_name,
        delivery=result.as_dict(),
        recovery=recovery if not result.ok else None,
    ).model_dump(exclude_none=True)


def queued_response(
    *,
    message: str,
    session_id: str | None = None,
    project_id: str | None = None,
    leader_session_id: str | None = None,
    provider: str | None = None,
) -> dict[str, Any]:
    """Response for async work that has only been queued, not verified."""

    return DeliveryStatusResponse(
        status="queued",
        delivery_state="queued",
        message=message,
        session_id=session_id,
        project_id=project_id,
        leader_session_id=leader_session_id,
        provider=provider,
        recovery="watch runtime/session status; queued does not prove delivery",
    ).model_dump(exclude_none=True)

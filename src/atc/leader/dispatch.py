"""Ace dispatch verification helpers.

Leader-owned dispatch verification keeps task assignment state separate from
runtime truth: an Ace assignment exists when a task is paired with a session,
but the task is only working after the Ace runtime accepts/starts the payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atc.runtime.models import BlockerReason, DeliveryState, RuntimeDeliveryResult, RuntimeState


@dataclass(slots=True)
class AceDispatchVerification:
    dispatch_verified: bool
    dispatch_delivery_state: str
    runtime_created: bool
    payload_written: bool
    submit_sent: bool
    provider_accepted: bool
    ace_began_work: bool
    assigned_task_id: str | None = None
    blocker_reason: str | None = None
    repair_recommendation: str | None = None
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "dispatch_verified": self.dispatch_verified,
            "dispatch_delivery_state": self.dispatch_delivery_state,
            "runtime_created": self.runtime_created,
            "payload_written": self.payload_written,
            "submit_sent": self.submit_sent,
            "provider_accepted": self.provider_accepted,
            "ace_began_work": self.ace_began_work,
        }
        if self.assigned_task_id:
            data["assigned_task_id"] = self.assigned_task_id
        if self.blocker_reason:
            data["blocker_reason"] = self.blocker_reason
        if self.repair_recommendation:
            data["repair_recommendation"] = self.repair_recommendation
        if self.message:
            data["message"] = self.message
        return data


def verify_ace_dispatch_delivery(
    result: RuntimeDeliveryResult | None,
    *,
    task_graph_id: str | None = None,
) -> AceDispatchVerification:
    """Convert runtime delivery output into provider-neutral Ace dispatch truth."""

    if result is None:
        return AceDispatchVerification(
            dispatch_verified=False,
            dispatch_delivery_state="queued_unverified",
            runtime_created=False,
            payload_written=False,
            submit_sent=False,
            provider_accepted=False,
            ace_began_work=False,
            assigned_task_id=task_graph_id,
            repair_recommendation="inspect Ace runtime before marking task working",
            message="Ace dispatch was queued but not observed",
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
    ace_began_work = (
        runtime_state is RuntimeState.ACTIVE or delivery is DeliveryState.ACCEPTED_ACTIVE
    )
    dispatch_verified = bool(result.ok and provider_accepted and ace_began_work)

    if blocker is not None or result.status in {"blocked", "failed"}:
        state = "blocked" if result.status == "blocked" else "failed"
    elif dispatch_verified:
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

    repair = None
    if not dispatch_verified:
        repair = "inspect Ace runtime and re-dispatch only if the payload is safe/idempotent"

    return AceDispatchVerification(
        dispatch_verified=dispatch_verified,
        dispatch_delivery_state=state,
        runtime_created=runtime_created,
        payload_written=payload_written,
        submit_sent=submit_sent,
        provider_accepted=provider_accepted,
        ace_began_work=ace_began_work,
        assigned_task_id=task_graph_id,
        blocker_reason=blocker.value if isinstance(blocker, BlockerReason) else None,
        repair_recommendation=repair,
        message=result.message,
    )

"""Provider-neutral Tower monitoring cadence policy.

This module intentionally consumes only runtime truth summaries. It does not
inspect provider panes and does not know provider-specific prompt text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from atc.runtime.models import RuntimeState

MonitorMode = Literal[
    "startup_verification",
    "leader_health",
    "leader_backoff",
    "inspect_or_recover",
]


@dataclass(frozen=True, slots=True)
class MonitoringCadenceDecision:
    """Cadence decision for Tower's provider-neutral monitoring loop."""

    mode: MonitorMode
    next_poll_seconds: int
    inspect_aces: bool
    should_nudge_leader: bool
    reason: str


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _age_seconds(value: str | None, now: datetime) -> int | None:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return None
    return max(0, int((now.astimezone(UTC) - parsed).total_seconds()))


def _leader_is_healthy(health: dict[str, Any]) -> bool:
    return (
        bool(health.get("runtime_exists"))
        and bool(health.get("pane_attached"))
        and health.get("runtime_state") in {RuntimeState.READY.value, RuntimeState.ACTIVE.value}
        and not health.get("current_blocker")
    )


def decide_tower_monitoring_cadence(
    health: dict[str, Any],
    *,
    startup_elapsed_seconds: int,
    now: datetime | None = None,
    user_requested_detail: bool = False,
    progress_flat_seconds: int | None = None,
) -> MonitoringCadenceDecision:
    """Decide how Tower should monitor a Leader/project.

    Tower watches Leader health and project-level outcome. It only asks for
    Ace-level detail when the provider-neutral summary says the Leader is
    blocked/missing, project progress is flat past a longer threshold, or the
    operator explicitly asks for detailed status.
    """

    current_now = now or datetime.now(UTC)
    blocker = health.get("current_blocker")
    runtime_state = health.get("runtime_state")
    leader_missing = not health.get("runtime_exists") or not health.get("pane_attached")
    healthy = _leader_is_healthy(health)
    activity_age = _age_seconds(health.get("last_activity_at"), current_now)
    task_state = health.get("task_graph_state") or {}
    task_total = int(task_state.get("total") or 0)

    if user_requested_detail:
        return MonitoringCadenceDecision(
            mode="inspect_or_recover",
            next_poll_seconds=0,
            inspect_aces=True,
            should_nudge_leader=False,
            reason="operator_requested_detailed_status",
        )

    if leader_missing and startup_elapsed_seconds < 120:
        return MonitoringCadenceDecision(
            mode="startup_verification",
            next_poll_seconds=10,
            inspect_aces=False,
            should_nudge_leader=startup_elapsed_seconds >= 30,
            reason="leader_kickoff_unverified",
        )

    if leader_missing:
        return MonitoringCadenceDecision(
            mode="inspect_or_recover",
            next_poll_seconds=30,
            inspect_aces=True,
            should_nudge_leader=False,
            reason="leader_runtime_missing",
        )

    if blocker or runtime_state in {RuntimeState.BLOCKED.value, RuntimeState.FAILED.value}:
        return MonitoringCadenceDecision(
            mode="inspect_or_recover",
            next_poll_seconds=60,
            inspect_aces=True,
            should_nudge_leader=False,
            reason=str(blocker or runtime_state or "leader_blocked"),
        )

    if progress_flat_seconds is not None and progress_flat_seconds >= 600:
        return MonitoringCadenceDecision(
            mode="inspect_or_recover",
            next_poll_seconds=60,
            inspect_aces=True,
            should_nudge_leader=True,
            reason="project_progress_flat_past_threshold",
        )

    if startup_elapsed_seconds < 120 and not healthy:
        return MonitoringCadenceDecision(
            mode="startup_verification",
            next_poll_seconds=10,
            inspect_aces=False,
            should_nudge_leader=startup_elapsed_seconds >= 30,
            reason="startup_kickoff_verification",
        )

    if healthy and activity_age is not None and activity_age < 300:
        return MonitoringCadenceDecision(
            mode="leader_backoff",
            next_poll_seconds=600 if task_total else 300,
            inspect_aces=False,
            should_nudge_leader=False,
            reason="leader_recently_active",
        )

    if healthy:
        should_nudge = activity_age is None or activity_age >= 600
        return MonitoringCadenceDecision(
            mode="leader_health",
            next_poll_seconds=300,
            inspect_aces=False,
            should_nudge_leader=should_nudge,
            reason="leader_no_recent_activity" if should_nudge else "leader_health_poll",
        )

    return MonitoringCadenceDecision(
        mode="startup_verification" if startup_elapsed_seconds < 120 else "inspect_or_recover",
        next_poll_seconds=30,
        inspect_aces=startup_elapsed_seconds >= 120,
        should_nudge_leader=startup_elapsed_seconds >= 30,
        reason="leader_kickoff_unverified",
    )

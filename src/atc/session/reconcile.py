"""Reconcile believed orchestration state against runtime reality.

Phase 6 runtime/orchestration hardening lives here: callers can run a dry
scan to get structured findings, or enable safe repairs for drift that ATC
can correct without operator judgement.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from atc.runtime.models import ReadinessState
from atc.runtime.service import RuntimeService
from atc.state import db as db_ops

if TYPE_CHECKING:
    import aiosqlite  # type: ignore[import-not-found]

FindingKind = Literal[
    "stale_active_session",
    "orphaned_task",
    "runtime_blocked",
    "provider_mismatch",
]
FindingSeverity = Literal["info", "warning", "error"]
RepairAction = Literal[
    "none",
    "mark_stale",
    "reset_task_for_reassignment",
    "require_operator_intervention",
]
RepairStatus = Literal["not_requested", "applied", "skipped", "failed"]

ACTIVE_SESSION_STATUSES = {"connecting", "idle", "working", "waiting", "paused"}
ACTIVE_TASK_STATUSES = {"assigned", "in_progress"}
ACTIVE_ASSIGNMENT_STATUSES = {"assigned", "working"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class ReconcileFinding:
    """Structured drift finding produced by a reconcile scan."""

    kind: FindingKind
    severity: FindingSeverity
    entity_type: str
    entity_id: str
    reason_code: str
    message: str
    recommended_action: RepairAction
    project_id: str | None = None
    session_id: str | None = None
    task_graph_id: str | None = None
    repair_status: RepairStatus = "not_requested"
    repair_error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ReconcileResult:
    """Result from a reconcile scan/repair pass."""

    repair: bool
    findings: list[ReconcileFinding]

    @property
    def summary(self) -> dict[str, int]:
        total = len(self.findings)
        applied = sum(1 for finding in self.findings if finding.repair_status == "applied")
        failed = sum(1 for finding in self.findings if finding.repair_status == "failed")
        by_kind: dict[str, int] = {}
        for finding in self.findings:
            by_kind[finding.kind] = by_kind.get(finding.kind, 0) + 1
        return {"total": total, "applied": applied, "failed": failed, **by_kind}

    def as_dict(self) -> dict[str, Any]:
        return {
            "repair": self.repair,
            "summary": self.summary,
            "findings": [finding.as_dict() for finding in self.findings],
        }


async def reconcile_runtime_state(
    conn: aiosqlite.Connection,
    *,
    repair: bool = False,
    runtime_service: RuntimeService | None = None,
) -> ReconcileResult:
    """Detect and optionally repair DB/runtime drift.

    Safe automated repairs currently cover:
    - active DB sessions whose tmux/provider runtime is no longer alive;
    - assigned/in-progress task graph entries with no bound live Ace.

    Unsafe drift, such as provider mismatch or runtime blockers, is reported as
    an operator-intervention finding and left unchanged.
    """

    service = runtime_service or RuntimeService()
    findings: list[ReconcileFinding] = []

    sessions = await db_ops.list_sessions(conn)
    session_by_id = {session.id: session for session in sessions}
    live_session_ids: set[str] = set()

    for session in sessions:
        if session.status not in ACTIVE_SESSION_STATUSES or not session.tmux_pane:
            continue
        try:
            inspection = await service.inspect_session_record(session)
        except Exception as exc:
            # Provider inspection failure means reality is unknown, not success.
            finding = ReconcileFinding(
                kind="stale_active_session",
                severity="warning",
                entity_type="session",
                entity_id=session.id,
                session_id=session.id,
                project_id=session.project_id,
                reason_code="runtime_inspection_failed",
                message="Active session could not be inspected by the runtime service.",
                recommended_action="require_operator_intervention",
                repair_status="skipped" if repair else "not_requested",
                details={
                    "status": session.status,
                    "tmux_pane": session.tmux_pane,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            findings.append(finding)
            if repair:
                await _audit_finding(conn, finding)
            continue

        if inspection.alive:
            live_session_ids.add(session.id)
            if inspection.readiness == ReadinessState.BLOCKED:
                finding = ReconcileFinding(
                    kind="runtime_blocked",
                    severity="warning",
                    entity_type="session",
                    entity_id=session.id,
                    session_id=session.id,
                    project_id=session.project_id,
                    reason_code=(
                        inspection.block_reason.value
                        if inspection.block_reason is not None
                        else "runtime_blocked"
                    ),
                    message="Runtime session is alive but blocked by provider/operator state.",
                    recommended_action="require_operator_intervention",
                    repair_status="skipped" if repair else "not_requested",
                    details={
                        "status": session.status,
                        "readiness": inspection.readiness.value,
                        "summary": inspection.summary,
                    },
                )
                findings.append(finding)
            continue

        finding = ReconcileFinding(
            kind="stale_active_session",
            severity="warning",
            entity_type="session",
            entity_id=session.id,
            session_id=session.id,
            project_id=session.project_id,
            reason_code="runtime_not_alive",
            message="DB marks session active, but runtime inspection reports it is not alive.",
            recommended_action="mark_stale",
            details={
                "status": session.status,
                "tmux_session": session.tmux_session,
                "tmux_pane": session.tmux_pane,
                "readiness": inspection.readiness.value,
                "summary": inspection.summary,
            },
        )
        findings.append(finding)
        if repair:
            await _mark_session_stale(conn, finding)

    current_provider = _current_default_provider(conn)
    if current_provider:
        for session in sessions:
            if (
                session.status in ACTIVE_SESSION_STATUSES
                and session.provider
                and session.provider != current_provider
            ):
                findings.append(
                    ReconcileFinding(
                        kind="provider_mismatch",
                        severity="warning",
                        entity_type="session",
                        entity_id=session.id,
                        session_id=session.id,
                        project_id=session.project_id,
                        reason_code="provider_mismatch",
                        message="Active session provider differs from current default provider.",
                        recommended_action="require_operator_intervention",
                        repair_status="skipped" if repair else "not_requested",
                        details={
                            "session_provider": session.provider,
                            "current_default_provider": current_provider,
                            "status": session.status,
                        },
                    )
                )

    for task in await db_ops.list_task_graphs(conn):
        if task.status not in ACTIVE_TASK_STATUSES:
            continue
        ace_id = task.assigned_ace_id
        ace = session_by_id.get(ace_id) if ace_id else None
        ace_live = (
            ace is not None
            and ace.session_type == "ace"
            and ace.id in live_session_ids
        )
        if ace_live:
            continue
        reason = "missing_assigned_ace" if not ace_id else "assigned_ace_not_live"
        finding = ReconcileFinding(
            kind="orphaned_task",
            severity="warning",
            entity_type="task_graph",
            entity_id=task.id,
            task_graph_id=task.id,
            session_id=ace_id,
            project_id=task.project_id,
            reason_code=reason,
            message="Task graph is active but has no bound live Ace session.",
            recommended_action="reset_task_for_reassignment",
            details={
                "task_status": task.status,
                "assigned_ace_id": ace_id,
                "ace_status": ace.status if ace is not None else None,
            },
        )
        findings.append(finding)
        if repair:
            await _reset_orphaned_task(conn, finding)

    return ReconcileResult(repair=repair, findings=findings)


async def _mark_session_stale(
    conn: aiosqlite.Connection,
    finding: ReconcileFinding,
) -> None:
    try:
        expected_status = str(finding.details.get("status") or "")
        expected_pane = finding.details.get("tmux_pane")
        now = _now()
        cursor = await conn.execute(
            """UPDATE sessions
               SET status = ?, tmux_session = NULL, tmux_pane = NULL, updated_at = ?
               WHERE id = ? AND status = ? AND tmux_pane IS ?""",
            ("disconnected", now, finding.entity_id, expected_status, expected_pane),
        )
        await conn.commit()
        if cursor.rowcount == 0:
            finding.repair_status = "skipped"
            finding.repair_error = "session changed before stale repair could apply"
        else:
            finding.repair_status = "applied"
        await _audit_finding(conn, finding)
    except Exception as exc:
        finding.repair_status = "failed"
        finding.repair_error = str(exc)
        await _audit_finding(conn, finding)


async def _reset_orphaned_task(
    conn: aiosqlite.Connection,
    finding: ReconcileFinding,
) -> None:
    try:
        task_id = finding.entity_id
        expected_assignee = finding.details.get("assigned_ace_id")
        task = await db_ops.get_task_graph(conn, task_id)
        if (
            task is None
            or task.status not in ACTIVE_TASK_STATUSES
            or task.assigned_ace_id != expected_assignee
        ):
            finding.repair_status = "skipped"
            finding.repair_error = "task assignment changed before orphan repair could apply"
            await _audit_finding(conn, finding)
            return

        for assignment in await db_ops.list_task_assignments(conn, task_graph_id=task_id):
            if assignment.status in ACTIVE_ASSIGNMENT_STATUSES:
                await db_ops.update_task_assignment_status(conn, assignment.assignment_id, "failed")
        task = await db_ops.get_task_graph(conn, task_id)
        if task is not None and task.status == "in_progress":
            await db_ops.update_task_graph_status(conn, task_id, "assigned")
        task = await db_ops.get_task_graph(conn, task_id)
        if task is not None and task.status == "assigned":
            await db_ops.update_task_graph_status(conn, task_id, "todo")
        await db_ops.update_task_graph(conn, task_id, assigned_ace_id=None)
        finding.repair_status = "applied"
        await _audit_finding(conn, finding)
    except Exception as exc:
        finding.repair_status = "failed"
        finding.repair_error = str(exc)
        await _audit_finding(conn, finding)


async def _audit_finding(conn: aiosqlite.Connection, finding: ReconcileFinding) -> None:
    await db_ops.create_app_event(
        conn,
        level=finding.severity,
        category="reconcile",
        message=finding.message,
        detail=finding.as_dict(),
        project_id=finding.project_id,
        session_id=finding.session_id,
    )


def _current_default_provider(conn: aiosqlite.Connection) -> str | None:
    app_state = db_ops.get_connection_app_state(conn)
    if app_state is None:
        app_state = getattr(getattr(conn, "_connection", None), "app_state", None)
    settings = getattr(app_state, "settings", None) if app_state is not None else None
    agent_provider = getattr(settings, "agent_provider", None) if settings is not None else None
    return getattr(agent_provider, "default", None)

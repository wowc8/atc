from __future__ import annotations

import pytest

from atc.orchestration.models import (
    CancelSessionRequest,
    ListSessionsRequest,
    OperationAcceptedResponse,
    OrchestrationRole,
    OrchestrationStatus,
    SendInstructionRequest,
    SessionEvent,
    SessionSummary,
    SpawnAceRequest,
    SpawnLeaderRequest,
    WaitForSessionRequest,
    normalize_role,
    normalize_status,
)


def test_normalize_role_tower() -> None:
    assert normalize_role("tower") == OrchestrationRole.TOWER


def test_normalize_role_manager_to_leader() -> None:
    assert normalize_role("manager") == OrchestrationRole.LEADER


def test_normalize_role_ace() -> None:
    assert normalize_role("ace") == OrchestrationRole.ACE


def test_normalize_role_unknown_raises() -> None:
    with pytest.raises(ValueError):
        normalize_role("unknown")


def test_normalize_status_connecting() -> None:
    assert normalize_status("connecting") == OrchestrationStatus.STARTING


def test_normalize_status_idle() -> None:
    assert normalize_status("idle") == OrchestrationStatus.READY


def test_normalize_status_working() -> None:
    assert normalize_status("working") == OrchestrationStatus.RUNNING


def test_normalize_status_waiting() -> None:
    assert normalize_status("waiting") == OrchestrationStatus.WAITING_INPUT


def test_normalize_status_paused() -> None:
    assert normalize_status("paused") == OrchestrationStatus.BLOCKED


def test_normalize_status_disconnected() -> None:
    assert normalize_status("disconnected") == OrchestrationStatus.FAILED


def test_normalize_status_error() -> None:
    assert normalize_status("error") == OrchestrationStatus.FAILED


def test_normalize_status_unknown_raises() -> None:
    with pytest.raises(ValueError):
        normalize_status("weird")


def test_spawn_leader_request_minimal() -> None:
    model = SpawnLeaderRequest(
        project_id="proj_123",
        goal="Ship thing",
        idempotency_key="op-1",
    )
    assert model.reuse_existing_idle is True
    assert model.require_clean_scope is False


def test_spawn_ace_request_minimal() -> None:
    model = SpawnAceRequest(
        project_id="proj_123",
        instruction="Implement feature",
        idempotency_key="op-2",
    )
    assert model.task_id is None


def test_send_instruction_request_minimal() -> None:
    model = SendInstructionRequest(
        session_id="sess_123",
        instruction="Continue",
        idempotency_key="msg-1",
    )
    assert model.await_delivery is True


def test_list_sessions_request_with_filters() -> None:
    model = ListSessionsRequest(
        project_id="proj_123",
        role=OrchestrationRole.LEADER,
        status_in=[OrchestrationStatus.READY, OrchestrationStatus.RUNNING],
        active_only=True,
        limit=5,
    )
    assert model.active_only is True
    assert model.limit == 5


def test_wait_for_session_request_default_timeout() -> None:
    model = WaitForSessionRequest(
        session_id="sess_123",
        target_statuses=[OrchestrationStatus.READY],
    )
    assert model.timeout_ms == 120_000


def test_cancel_session_request_defaults() -> None:
    model = CancelSessionRequest(session_id="sess_123")
    assert model.force is False
    assert model.reason is None


def test_session_summary_accepts_raw_and_normalized_fields() -> None:
    model = SessionSummary(
        id="sess_123",
        role=OrchestrationRole.LEADER,
        raw_session_type="manager",
        project_id="proj_123",
        status=OrchestrationStatus.READY,
        raw_status="idle",
        name="leader-ATC",
        created_at="2026-05-25T05:48:00Z",
        updated_at="2026-05-25T05:49:00Z",
    )
    assert model.raw_session_type == "manager"
    assert model.status == OrchestrationStatus.READY


def test_operation_accepted_response_minimal() -> None:
    model = OperationAcceptedResponse(request_status="accepted", operation_id="op_123")
    assert model.operation_id == "op_123"


def test_session_event_shape() -> None:
    model = SessionEvent(
        id="evt_123",
        session_id="sess_123",
        event_type="session_created",
        created_at="2026-05-25T05:50:00Z",
        data={"k": "v"},
    )
    assert model.data == {"k": "v"}

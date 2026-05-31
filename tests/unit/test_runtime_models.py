from __future__ import annotations

import pytest

from atc.runtime.models import (
    InstructionRequest,
    ReadinessState,
    RoleKind,
    RuntimeTransport,
    TaskAssignmentRequest,
    WrapperExitCode,
)


def test_role_kind_values() -> None:
    assert RoleKind.TOWER.value == "tower"
    assert RoleKind.LEADER.value == "leader"
    assert RoleKind.ACE.value == "ace"


def test_runtime_transport_value() -> None:
    assert RuntimeTransport.TMUX.value == "tmux"


def test_readiness_state_ready_value() -> None:
    assert ReadinessState.READY.value == "ready"


def test_wrapper_exit_code_success() -> None:
    assert int(WrapperExitCode.SUCCESS) == 0


def test_instruction_request_requires_message_or_file() -> None:
    with pytest.raises(ValueError):
        InstructionRequest(session_id="sess-1")


def test_task_assignment_request_requires_message_or_file() -> None:
    with pytest.raises(ValueError):
        TaskAssignmentRequest(session_id="sess-1", task_id="task-1")

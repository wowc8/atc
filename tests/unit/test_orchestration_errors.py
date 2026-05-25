from __future__ import annotations

from atc.orchestration.errors import OrchestrationErrorCode, OrchestrationException


def test_orchestration_exception_serializes() -> None:
    exc = OrchestrationException(
        OrchestrationErrorCode.PROJECT_NOT_FOUND,
        "Project missing",
        details={"project_id": "proj_123"},
    )
    assert exc.to_dict() == {
        "code": "PROJECT_NOT_FOUND",
        "message": "Project missing",
        "retryable": False,
        "details": {"project_id": "proj_123"},
    }


def test_retryable_error_preserved() -> None:
    exc = OrchestrationException(
        OrchestrationErrorCode.PROVIDER_UNAVAILABLE,
        "Provider offline",
        retryable=True,
    )
    assert exc.retryable is True
    assert exc.http_status == 503


def test_details_default_empty() -> None:
    exc = OrchestrationException(
        OrchestrationErrorCode.INVALID_REQUEST,
        "Bad request",
    )
    assert exc.details == {}


def test_error_code_values_stable() -> None:
    assert OrchestrationErrorCode.IDEMPOTENCY_CONFLICT.value == "IDEMPOTENCY_CONFLICT"
    assert OrchestrationErrorCode.BUDGET_BLOCKED.value == "BUDGET_BLOCKED"

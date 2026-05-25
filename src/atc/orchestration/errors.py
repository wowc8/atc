from __future__ import annotations

from enum import Enum
from typing import Any


class OrchestrationErrorCode(str, Enum):
    PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    INVALID_ROLE = "INVALID_ROLE"
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_PARENT_RELATION = "INVALID_PARENT_RELATION"
    SESSION_NOT_READY = "SESSION_NOT_READY"
    SESSION_NOT_ACTIVE = "SESSION_NOT_ACTIVE"
    DELIVERY_FAILED = "DELIVERY_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_AUTH_FAILED = "PROVIDER_AUTH_FAILED"
    CONCURRENCY_LIMIT_REACHED = "CONCURRENCY_LIMIT_REACHED"
    BUDGET_BLOCKED = "BUDGET_BLOCKED"
    SESSION_FAILED = "SESSION_FAILED"
    DUPLICATE_IDEMPOTENCY_KEY = "DUPLICATE_IDEMPOTENCY_KEY"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    SQLITE_LOCK_TIMEOUT = "SQLITE_LOCK_TIMEOUT"
    INTERNAL_STORAGE_ERROR = "INTERNAL_STORAGE_ERROR"


_DEFAULT_HTTP_STATUS: dict[OrchestrationErrorCode, int] = {
    OrchestrationErrorCode.PROJECT_NOT_FOUND: 404,
    OrchestrationErrorCode.SESSION_NOT_FOUND: 404,
    OrchestrationErrorCode.TASK_NOT_FOUND: 404,
    OrchestrationErrorCode.INVALID_ROLE: 400,
    OrchestrationErrorCode.INVALID_REQUEST: 400,
    OrchestrationErrorCode.INVALID_PARENT_RELATION: 409,
    OrchestrationErrorCode.SESSION_NOT_READY: 409,
    OrchestrationErrorCode.SESSION_NOT_ACTIVE: 409,
    OrchestrationErrorCode.DELIVERY_FAILED: 409,
    OrchestrationErrorCode.VERIFICATION_FAILED: 409,
    OrchestrationErrorCode.PROVIDER_UNAVAILABLE: 503,
    OrchestrationErrorCode.PROVIDER_AUTH_FAILED: 503,
    OrchestrationErrorCode.CONCURRENCY_LIMIT_REACHED: 409,
    OrchestrationErrorCode.BUDGET_BLOCKED: 409,
    OrchestrationErrorCode.SESSION_FAILED: 409,
    OrchestrationErrorCode.DUPLICATE_IDEMPOTENCY_KEY: 200,
    OrchestrationErrorCode.IDEMPOTENCY_CONFLICT: 409,
    OrchestrationErrorCode.SQLITE_LOCK_TIMEOUT: 503,
    OrchestrationErrorCode.INTERNAL_STORAGE_ERROR: 500,
}


class OrchestrationException(Exception):
    def __init__(
        self,
        code: OrchestrationErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        http_status: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        self.http_status = http_status if http_status is not None else _DEFAULT_HTTP_STATUS[code]
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }

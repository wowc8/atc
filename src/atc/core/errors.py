"""Domain-specific error types for ATC.

Each error carries a machine-readable ``code`` that the frontend maps to
appropriate UI (reconnect button, retry timer, re-login prompt, etc.).

Hierarchy
---------
ATCError (base)
├── AgentError
│   ├── SessionNotFoundError
│   ├── SessionStaleError
│   └── CreationFailedError
├── BudgetError
│   ├── LimitExceededError
│   └── NoBudgetSetError
└── GitHubError
    ├── RateLimitedError
    └── AuthFailedError
"""

from __future__ import annotations

from typing import Any


class ATCError(Exception):
    """Base error for all ATC domain exceptions.

    Attributes:
        code: Machine-readable error code (e.g. ``session_not_found``).
        status_code: Suggested HTTP status code for API responses.
        detail: Human-readable message.
        extra: Optional dict of additional context for the frontend.
    """

    code: str = "internal_error"
    status_code: int = 500

    def __init__(
        self,
        detail: str | None = None,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.detail = detail or self.__class__.__doc__ or self.code
        self.extra: dict[str, Any] = extra or {}
        super().__init__(self.detail)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the structured JSON envelope the frontend expects."""
        payload: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.detail,
            },
        }
        if self.extra:
            payload["error"]["extra"] = self.extra
        return payload


# ---------------------------------------------------------------------------
# Agent / session errors
# ---------------------------------------------------------------------------


class AgentError(ATCError):
    """Base for agent/session-related errors."""

    code = "agent_error"
    status_code = 500


class SessionNotFoundError(AgentError):
    """The requested session does not exist."""

    code = "session_not_found"
    status_code = 404


class SessionStaleError(AgentError):
    """The session's state has changed since the client last fetched it."""

    code = "session_stale"
    status_code = 409


class CreationFailedError(AgentError):
    """Failed to create an agent session."""

    code = "creation_failed"
    status_code = 500


# ---------------------------------------------------------------------------
# Budget errors
# ---------------------------------------------------------------------------


class BudgetError(ATCError):
    """Base for budget-related errors."""

    code = "budget_error"
    status_code = 402


class LimitExceededError(BudgetError):
    """The budget limit has been exceeded."""

    code = "budget_limit_exceeded"
    status_code = 402


class NoBudgetSetError(BudgetError):
    """No budget has been configured for this project."""

    code = "no_budget_set"
    status_code = 404


# ---------------------------------------------------------------------------
# GitHub errors
# ---------------------------------------------------------------------------


class GitHubError(ATCError):
    """Base for GitHub integration errors."""

    code = "github_error"
    status_code = 502


class RateLimitedError(GitHubError):
    """GitHub API rate limit has been hit."""

    code = "github_rate_limited"
    status_code = 429

    def __init__(
        self,
        detail: str | None = None,
        *,
        retry_after: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = dict(extra or {})
        if retry_after is not None:
            merged["retry_after"] = retry_after
        super().__init__(detail, extra=merged)


class AuthFailedError(GitHubError):
    """GitHub authentication failed or token expired."""

    code = "github_auth_failed"
    status_code = 401

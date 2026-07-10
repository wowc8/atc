from __future__ import annotations

import pytest

from atc.providers.helpers import (
    ProviderHelperEventType,
    ProviderHelperParentRole,
    ProviderHelperRequest,
    ProviderHelperResult,
    ProviderHelperRunStatus,
    ProviderHelperVisibility,
)


def test_provider_helper_request_normalizes_enums_and_tuples() -> None:
    request = ProviderHelperRequest(
        provider="codex",
        parent_session_id="session-1",
        parent_role="leader",
        purpose="inspect_blockers",
        prompt="Inspect blockers",
        visibility="summary",
        allowed_tools=["read"],  # type: ignore[arg-type]
        allowed_actions=["comment"],  # type: ignore[arg-type]
    )

    assert request.parent_role is ProviderHelperParentRole.LEADER
    assert request.visibility is ProviderHelperVisibility.SUMMARY
    assert request.allowed_tools == ("read",)
    assert request.allowed_actions == ("comment",)


@pytest.mark.parametrize(
    ("field", "kwargs"),
    [
        ("provider", {"provider": ""}),
        ("parent_session_id", {"parent_session_id": ""}),
        ("purpose", {"purpose": ""}),
        ("prompt", {"prompt": ""}),
    ],
)
def test_provider_helper_request_requires_core_fields(field: str, kwargs: dict[str, str]) -> None:
    base = dict(
        provider="codex",
        parent_session_id="session-1",
        parent_role="ace",
        purpose="purpose",
        prompt="prompt",
    )
    base.update(kwargs)

    with pytest.raises(ValueError, match=f"{field} is required"):
        ProviderHelperRequest(**base)


def test_provider_helper_result_normalizes_status() -> None:
    result = ProviderHelperResult(
        helper_run_id="run-1",
        status="completed",
        summary="done",
    )

    assert result.status is ProviderHelperRunStatus.COMPLETED
    assert result.summary == "done"


def test_known_event_types_are_provider_neutral() -> None:
    assert ProviderHelperEventType.HELPER_REQUESTED == "helper_requested"
    assert ProviderHelperEventType.TOKEN_USAGE_RECORDED == "token_usage_recorded"

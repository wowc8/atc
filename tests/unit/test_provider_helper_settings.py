from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from atc.api.routers.settings import (
    ProviderHelpersUpdateRequest,
    get_provider_helpers,
    update_provider_helpers,
)
from atc.config import Settings


def fake_request(settings: Settings):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))


@pytest.mark.asyncio
async def test_provider_helper_settings_defaults_and_update() -> None:
    settings = Settings()
    request = fake_request(settings)

    defaults = await get_provider_helpers(request)
    assert defaults.enabled is True
    assert defaults.default_visibility == "hidden"
    assert defaults.audit_enabled is True

    updated = await update_provider_helpers(
        ProviderHelpersUpdateRequest(enabled=False, default_visibility="summary"),
        request,
    )

    assert updated.enabled is False
    assert updated.default_visibility == "summary"
    assert updated.audit_enabled is True
    assert settings.provider_helpers.enabled is False
    assert settings.provider_helpers.default_visibility == "summary"
    assert settings.provider_helpers.audit_enabled is True


@pytest.mark.asyncio
async def test_provider_helper_settings_reject_invalid_visibility() -> None:
    settings = Settings()

    with pytest.raises(HTTPException) as exc:
        await update_provider_helpers(
            ProviderHelpersUpdateRequest(default_visibility="operator-only"),
            fake_request(settings),
        )

    assert exc.value.status_code == 422
    assert settings.provider_helpers.default_visibility == "hidden"

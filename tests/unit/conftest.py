"""Unit-test fixtures for isolation of global state."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def reset_global_ace_counter():
    """Reset the module-level Ace counter and lock before/after each test."""
    import atc.leader.orchestrator as orch_mod

    orch_mod._GLOBAL_ACTIVE_ACES = 0
    orch_mod._GLOBAL_LOCK = None
    yield
    orch_mod._GLOBAL_ACTIVE_ACES = 0
    orch_mod._GLOBAL_LOCK = None


@pytest.fixture(autouse=True)
def mock_low_system_usage():
    """Pin CPU/RAM usage to 0% so ResourceGovernor never throttles during tests."""
    with patch(
        "atc.tracking.resources.ResourceGovernor.get_system_usage",
        return_value=(0.0, 0.0),
    ):
        yield

"""Unit tests for creation reliability features (design doc §10a).

Tests the current verification contract:
- ``verify_alive`` is the hard liveness gate
- ``verify_working`` and ``verify_progressing`` are tmux-output heuristics
- session-lifecycle invariants stay intact across those checks
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from atc.session.ace import VerificationResult, _get_alternate_on, verify_alive, verify_progressing, verify_working
from atc.state.models import Session


def _make_session(
    *,
    status: str = "idle",
    tmux_pane: str | None = "%0",
    session_id: str = "test-session-1",
) -> Session:
    return Session(
        id=session_id,
        project_id="proj-1",
        session_type="ace",
        name="test-ace",
        status=status,
        tmux_pane=tmux_pane,
        tmux_session="atc",
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )


class TestGetAlternateOn:
    @pytest.mark.asyncio
    @patch("atc.session.ace._tmux_run", new_callable=AsyncMock)
    async def test_alternate_on_true(self, mock_tmux: AsyncMock) -> None:
        mock_tmux.return_value = "1"
        assert await _get_alternate_on("%0") is True
        mock_tmux.assert_called_once_with("display-message", "-t", "%0", "-p", "#{alternate_on}")

    @pytest.mark.asyncio
    @patch("atc.session.ace._tmux_run", new_callable=AsyncMock)
    async def test_alternate_on_false(self, mock_tmux: AsyncMock) -> None:
        mock_tmux.return_value = "0"
        assert await _get_alternate_on("%0") is False


class TestVerifyAlive:
    @pytest.mark.asyncio
    @patch("atc.session.ace._pane_is_alive", new_callable=AsyncMock)
    @patch("atc.session.ace.db_ops.get_session", new_callable=AsyncMock)
    async def test_healthy(self, mock_get: AsyncMock, mock_alive: AsyncMock) -> None:
        mock_get.return_value = _make_session()
        mock_alive.return_value = True
        result = await verify_alive(AsyncMock(), "test-session-1")
        assert result.ok is True
        assert result.phase == "alive"

    @pytest.mark.asyncio
    @patch("atc.session.ace.db_ops.get_session", new_callable=AsyncMock)
    async def test_not_found(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = None
        result = await verify_alive(AsyncMock(), "missing")
        assert result.ok is False
        assert "not found" in result.detail

    @pytest.mark.asyncio
    @patch("atc.session.ace.db_ops.get_session", new_callable=AsyncMock)
    async def test_error_status(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = _make_session(status="error")
        result = await verify_alive(AsyncMock(), "test-session-1")
        assert result.ok is False

    @pytest.mark.asyncio
    @patch("atc.session.ace.db_ops.get_session", new_callable=AsyncMock)
    async def test_no_pane(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = _make_session(tmux_pane=None)
        result = await verify_alive(AsyncMock(), "test-session-1")
        assert result.ok is False
        assert "no tmux pane" in result.detail

    @pytest.mark.asyncio
    @patch("atc.session.ace._pane_is_alive", new_callable=AsyncMock)
    @patch("atc.session.ace.db_ops.get_session", new_callable=AsyncMock)
    async def test_dead_pane(self, mock_get: AsyncMock, mock_alive: AsyncMock) -> None:
        mock_get.return_value = _make_session()
        mock_alive.return_value = False
        result = await verify_alive(AsyncMock(), "test-session-1")
        assert result.ok is False
        assert "dead" in result.detail


class TestVerifyWorking:
    @pytest.mark.asyncio
    @patch("atc.session.ace.db_ops.get_session", new_callable=AsyncMock)
    async def test_active_status(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = _make_session(status="working")
        result = await verify_working(AsyncMock(), "test-session-1")
        assert result.ok is True

    @pytest.mark.asyncio
    @patch("atc.session.ace._capture_pane", new_callable=AsyncMock)
    @patch("atc.session.ace.db_ops.get_session", new_callable=AsyncMock)
    async def test_output_counts_as_activity(self, mock_get: AsyncMock, mock_capture: AsyncMock) -> None:
        mock_get.return_value = _make_session(status="idle")
        mock_capture.return_value = "some output"
        result = await verify_working(AsyncMock(), "test-session-1")
        assert result.ok is True
        assert result.detail == "pane has output"

    @pytest.mark.asyncio
    @patch("atc.session.ace._capture_pane", new_callable=AsyncMock)
    @patch("atc.session.ace.db_ops.get_session", new_callable=AsyncMock)
    async def test_no_activity(self, mock_get: AsyncMock, mock_capture: AsyncMock) -> None:
        mock_get.return_value = _make_session(status="idle")
        mock_capture.return_value = "   "
        result = await verify_working(AsyncMock(), "test-session-1")
        assert result.ok is False
        assert "no activity" in result.detail

    @pytest.mark.asyncio
    @patch("atc.session.ace._capture_pane", new_callable=AsyncMock)
    @patch("atc.session.ace.db_ops.get_session", new_callable=AsyncMock)
    async def test_capture_failure_is_handled(self, mock_get: AsyncMock, mock_capture: AsyncMock) -> None:
        mock_get.return_value = _make_session(status="idle")
        mock_capture.side_effect = RuntimeError("tmux failed")
        result = await verify_working(AsyncMock(), "test-session-1")
        assert result.ok is False

    @pytest.mark.asyncio
    @patch("atc.session.ace.db_ops.get_session", new_callable=AsyncMock)
    async def test_not_found(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = None
        result = await verify_working(AsyncMock(), "missing")
        assert result.ok is False


class TestVerifyProgressing:
    @pytest.mark.asyncio
    @patch("atc.session.ace._capture_pane", new_callable=AsyncMock)
    @patch("atc.session.ace.db_ops.get_session", new_callable=AsyncMock)
    async def test_changed_output(self, mock_get: AsyncMock, mock_capture: AsyncMock) -> None:
        mock_get.return_value = _make_session(status="working")
        mock_capture.return_value = "new output"
        result = await verify_progressing(
            AsyncMock(),
            "test-session-1",
            previous_output="old output",
        )
        assert result.ok is True

    @pytest.mark.asyncio
    @patch("atc.session.ace._capture_pane", new_callable=AsyncMock)
    @patch("atc.session.ace.db_ops.get_session", new_callable=AsyncMock)
    async def test_unchanged_output(self, mock_get: AsyncMock, mock_capture: AsyncMock) -> None:
        mock_get.return_value = _make_session(status="working")
        mock_capture.return_value = "same output"
        result = await verify_progressing(
            AsyncMock(),
            "test-session-1",
            previous_output="same output",
        )
        assert result.ok is False
        assert "unchanged" in result.detail

    @pytest.mark.asyncio
    @patch("atc.session.ace.db_ops.get_session", new_callable=AsyncMock)
    async def test_error_status(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = _make_session(status="error")
        result = await verify_progressing(AsyncMock(), "test-session-1")
        assert result.ok is False

    @pytest.mark.asyncio
    @patch("atc.session.ace.db_ops.get_session", new_callable=AsyncMock)
    async def test_no_pane(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = _make_session(tmux_pane=None)
        result = await verify_progressing(AsyncMock(), "test-session-1")
        assert result.ok is False

    @pytest.mark.asyncio
    @patch("atc.session.ace._capture_pane", new_callable=AsyncMock)
    @patch("atc.session.ace.db_ops.get_session", new_callable=AsyncMock)
    async def test_capture_failure(self, mock_get: AsyncMock, mock_capture: AsyncMock) -> None:
        mock_get.return_value = _make_session(status="working")
        mock_capture.side_effect = RuntimeError("tmux failed")
        result = await verify_progressing(AsyncMock(), "test-session-1")
        assert result.ok is False
        assert "capture-pane failed" in result.detail

    @pytest.mark.asyncio
    @patch("atc.session.ace._capture_pane", new_callable=AsyncMock)
    @patch("atc.session.ace.db_ops.get_session", new_callable=AsyncMock)
    async def test_error_pattern_traceback(self, mock_get: AsyncMock, mock_capture: AsyncMock) -> None:
        mock_get.return_value = _make_session(status="working")
        mock_capture.return_value = "Traceback (most recent call last): ..."
        result = await verify_progressing(AsyncMock(), "test-session-1")
        assert result.ok is False
        assert "error pattern" in result.detail

    @pytest.mark.asyncio
    @patch("atc.session.ace._capture_pane", new_callable=AsyncMock)
    @patch("atc.session.ace.db_ops.get_session", new_callable=AsyncMock)
    async def test_error_pattern_permission_denied(self, mock_get: AsyncMock, mock_capture: AsyncMock) -> None:
        mock_get.return_value = _make_session(status="working")
        mock_capture.return_value = "Permission denied when opening file"
        result = await verify_progressing(AsyncMock(), "test-session-1")
        assert result.ok is False
        assert "permission denied" in result.detail.lower()


class TestVerificationResult:
    def test_dataclass(self) -> None:
        result = VerificationResult(ok=True, phase="alive", detail="all good")
        assert result.ok is True
        assert result.phase == "alive"
        assert result.detail == "all good"

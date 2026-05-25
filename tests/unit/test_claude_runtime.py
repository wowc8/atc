from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from atc.agents.claude_runtime import check_tui_ready, send_instruction, wait_for_prompt


@pytest.mark.asyncio
async def test_wait_for_prompt_returns_true_on_bare_prompt() -> None:
    result = await wait_for_prompt(
        "pane-1",
        get_alternate_on=AsyncMock(return_value=False),
        capture_pane=AsyncMock(return_value="❯"),
        timeout=2.0,
    )
    assert result is True


@pytest.mark.asyncio
async def test_wait_for_prompt_returns_true_on_gt_prompt() -> None:
    result = await wait_for_prompt(
        "pane-2",
        get_alternate_on=AsyncMock(return_value=False),
        capture_pane=AsyncMock(return_value="> "),
        timeout=2.0,
    )
    assert result is True


@pytest.mark.asyncio
async def test_wait_for_prompt_waits_while_alternate_on() -> None:
    result = await wait_for_prompt(
        "pane-3",
        get_alternate_on=AsyncMock(side_effect=[True, True, False]),
        capture_pane=AsyncMock(return_value="❯"),
        timeout=5.0,
        poll_interval=0.1,
    )
    assert result is True


@pytest.mark.asyncio
async def test_wait_for_prompt_returns_false_on_timeout() -> None:
    result = await wait_for_prompt(
        "pane-4",
        get_alternate_on=AsyncMock(return_value=False),
        capture_pane=AsyncMock(return_value="some output without prompt"),
        timeout=0.1,
        poll_interval=0.05,
    )
    assert result is False


@pytest.mark.asyncio
async def test_wait_for_prompt_returns_false_on_runtime_error() -> None:
    result = await wait_for_prompt(
        "pane-5",
        get_alternate_on=AsyncMock(side_effect=RuntimeError("pane dead")),
        capture_pane=AsyncMock(),
        timeout=2.0,
    )
    assert result is False


@pytest.mark.asyncio
async def test_check_tui_ready_immediately() -> None:
    result = await check_tui_ready(
        "%0",
        get_alternate_on=AsyncMock(return_value=False),
        timeout=1.0,
        poll_interval=0.1,
    )
    assert result is True


@pytest.mark.asyncio
async def test_check_tui_ready_after_delay() -> None:
    mock_alt = AsyncMock(side_effect=[True, True, False])
    result = await check_tui_ready(
        "%0",
        get_alternate_on=mock_alt,
        timeout=5.0,
        poll_interval=0.1,
    )
    assert result is True
    assert mock_alt.call_count == 3


@pytest.mark.asyncio
async def test_check_tui_ready_timeout() -> None:
    result = await check_tui_ready(
        "%0",
        get_alternate_on=AsyncMock(return_value=True),
        timeout=0.3,
        poll_interval=0.1,
    )
    assert result is False


@pytest.mark.asyncio
async def test_check_tui_ready_pane_dies() -> None:
    result = await check_tui_ready(
        "%0",
        get_alternate_on=AsyncMock(side_effect=RuntimeError("pane dead")),
        timeout=1.0,
        poll_interval=0.1,
    )
    assert result is False


class TestSendInstruction:
    @pytest.mark.asyncio
    @patch("atc.agents.claude_runtime.send_instruction_async", new_callable=AsyncMock)
    async def test_success(self, mock_send_async: AsyncMock) -> None:
        result = await send_instruction(
            "%0",
            "do something important",
            capture_pane=AsyncMock(return_value="$ do something important\noutput here"),
            pane_is_alive=AsyncMock(return_value=True),
            wait_for_prompt_fn=AsyncMock(return_value=True),
            check_tui_ready_fn=AsyncMock(return_value=True),
            max_retries=1,
        )
        assert result is True
        mock_send_async.assert_called_once_with("atc", "%0", "do something important")

    @pytest.mark.asyncio
    async def test_tui_not_ready(self) -> None:
        result = await send_instruction(
            "%0",
            "test",
            capture_pane=AsyncMock(),
            pane_is_alive=AsyncMock(),
            wait_for_prompt_fn=AsyncMock(return_value=False),
            check_tui_ready_fn=AsyncMock(return_value=False),
            max_retries=2,
        )
        assert result is False

    @pytest.mark.asyncio
    @patch("atc.agents.claude_runtime.send_instruction_async", new_callable=AsyncMock)
    async def test_retry_on_verification_failure(self, mock_send_async: AsyncMock) -> None:
        result = await send_instruction(
            "%0",
            "run tests",
            capture_pane=AsyncMock(side_effect=["$ \n", "$ run tests\nrunning..."]),
            pane_is_alive=AsyncMock(return_value=True),
            wait_for_prompt_fn=AsyncMock(return_value=True),
            check_tui_ready_fn=AsyncMock(return_value=True),
            max_retries=2,
        )
        assert result is True
        assert mock_send_async.call_count == 2

    @pytest.mark.asyncio
    @patch("atc.agents.claude_runtime.send_instruction_async", new_callable=AsyncMock)
    async def test_skip_verification(self, mock_send_async: AsyncMock) -> None:
        result = await send_instruction(
            "%0",
            "test",
            capture_pane=AsyncMock(),
            pane_is_alive=AsyncMock(),
            wait_for_prompt_fn=AsyncMock(return_value=True),
            check_tui_ready_fn=AsyncMock(return_value=True),
            verify=False,
        )
        assert result is True
        mock_send_async.assert_called_once_with("atc", "%0", "test")

    @pytest.mark.asyncio
    @patch("atc.agents.claude_runtime.send_instruction_async", new_callable=AsyncMock)
    async def test_prompt_disappearing_counts_as_accepted_delivery(self, mock_send_async: AsyncMock) -> None:
        result = await send_instruction(
            "%0",
            "run tests",
            capture_pane=AsyncMock(return_value="$ \n"),
            pane_is_alive=AsyncMock(return_value=True),
            wait_for_prompt_fn=AsyncMock(side_effect=[True, False]),
            check_tui_ready_fn=AsyncMock(return_value=True),
            max_retries=1,
        )
        assert result is True
        mock_send_async.assert_called_once_with("atc", "%0", "run tests")

    @pytest.mark.asyncio
    @patch("atc.agents.claude_runtime.send_instruction_async", new_callable=AsyncMock)
    async def test_prompt_disappearing_with_dead_pane_is_not_treated_as_success(self, mock_send_async: AsyncMock) -> None:
        result = await send_instruction(
            "%0",
            "run tests",
            capture_pane=AsyncMock(return_value="$ \n"),
            pane_is_alive=AsyncMock(return_value=False),
            wait_for_prompt_fn=AsyncMock(side_effect=[True, False]),
            check_tui_ready_fn=AsyncMock(return_value=True),
            max_retries=1,
        )
        assert result is False
        mock_send_async.assert_called_once_with("atc", "%0", "run tests")

    @pytest.mark.asyncio
    @patch("atc.agents.claude_runtime.send_instruction_async", new_callable=AsyncMock)
    async def test_prompt_disappearing_with_visible_dialog_is_not_treated_as_success(self, mock_send_async: AsyncMock) -> None:
        async def capture_side_effect(*args, **kwargs):
            capture_side_effect.calls += 1
            if capture_side_effect.calls == 1:
                return "$ \\n"
            return "[Pasted text #1 +21 lines]\\nbypass permissions on (shift+tab to cycle)\\n"

        capture_side_effect.calls = 0
        result = await send_instruction(
            "%0",
            "run tests",
            capture_pane=AsyncMock(side_effect=capture_side_effect),
            pane_is_alive=AsyncMock(return_value=True),
            wait_for_prompt_fn=AsyncMock(side_effect=[True, False]),
            check_tui_ready_fn=AsyncMock(return_value=True),
            max_retries=1,
        )
        assert result is False
        mock_send_async.assert_called_once_with("atc", "%0", "run tests")

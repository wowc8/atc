from __future__ import annotations

from atc.runtime.errors import (
    RuntimeBlockedError,
    RuntimeDeliveryError,
    RuntimeInvocationError,
    RuntimeNotReadyError,
    RuntimeRestoreError,
    RuntimeSessionMissingError,
    map_wrapper_exit_code,
)
from atc.runtime.models import WrapperExitCode


def test_map_wrapper_exit_code_success_returns_none() -> None:
    assert map_wrapper_exit_code(WrapperExitCode.SUCCESS, message="ok") is None


def test_map_wrapper_exit_code_blocked_auth() -> None:
    err = map_wrapper_exit_code(WrapperExitCode.BLOCKED_AUTH, message="blocked")
    assert isinstance(err, RuntimeBlockedError)


def test_map_wrapper_exit_code_not_ready() -> None:
    err = map_wrapper_exit_code(WrapperExitCode.NOT_READY, message="not ready")
    assert isinstance(err, RuntimeNotReadyError)


def test_map_wrapper_exit_code_session_missing() -> None:
    err = map_wrapper_exit_code(WrapperExitCode.SESSION_MISSING, message="missing")
    assert isinstance(err, RuntimeSessionMissingError)


def test_map_wrapper_exit_code_delivery_failed() -> None:
    err = map_wrapper_exit_code(WrapperExitCode.DELIVERY_FAILED, message="delivery failed")
    assert isinstance(err, RuntimeDeliveryError)


def test_map_wrapper_exit_code_restore_failed() -> None:
    err = map_wrapper_exit_code(WrapperExitCode.RESTORE_FAILED, message="restore failed")
    assert isinstance(err, RuntimeRestoreError)


def test_map_wrapper_exit_code_invalid_args() -> None:
    err = map_wrapper_exit_code(WrapperExitCode.INVALID_ARGS, message="invalid")
    assert isinstance(err, RuntimeInvocationError)

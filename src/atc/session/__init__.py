"""Session subsystem exports.

Keep this package import-light so callers can import submodules like
`atc.session.state_machine` or `atc.session.reconnect` without eagerly
pulling in the full ace lifecycle graph and triggering circular imports.
"""

from atc.session.state_machine import (
    InvalidTransitionError,
    SessionStatus,
    is_valid_transition,
    transition,
)

__all__ = [
    "InvalidTransitionError",
    "SessionStatus",
    "create_ace",
    "destroy_ace",
    "is_valid_transition",
    "schedule_verification",
    "start_ace",
    "stop_ace",
    "transition",
    "verify_alive",
    "verify_progressing",
    "verify_session",
    "verify_working",
]


def __getattr__(name: str):
    if name in {
        "create_ace",
        "destroy_ace",
        "schedule_verification",
        "start_ace",
        "stop_ace",
        "verify_alive",
        "verify_progressing",
        "verify_session",
        "verify_working",
    }:
        from atc.session.ace import (
            create_ace,
            destroy_ace,
            schedule_verification,
            start_ace,
            stop_ace,
            verify_alive,
            verify_progressing,
            verify_session,
            verify_working,
        )

        mapping = {
            "create_ace": create_ace,
            "destroy_ace": destroy_ace,
            "schedule_verification": schedule_verification,
            "start_ace": start_ace,
            "stop_ace": stop_ace,
            "verify_alive": verify_alive,
            "verify_progressing": verify_progressing,
            "verify_session": verify_session,
            "verify_working": verify_working,
        }
        return mapping[name]
    raise AttributeError(name)

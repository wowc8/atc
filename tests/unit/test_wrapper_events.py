from __future__ import annotations

import pytest

from atc.runtime.wrapper_events import WrapperEventParseError, parse_wrapper_event


def test_parse_wrapper_event_success() -> None:
    event = parse_wrapper_event(
        'ATC_EVENT runtime_ready {"session_id":"sess_1","provider":"codex","command":"start-role"}'
    )

    assert event.name == "runtime_ready"
    assert event.session_id == "sess_1"
    assert event.provider == "codex"
    assert event.command == "start-role"


def test_parse_wrapper_event_requires_prefix() -> None:
    with pytest.raises(WrapperEventParseError):
        parse_wrapper_event('runtime_ready {"session_id":"sess_1","provider":"codex","command":"start-role"}')


def test_parse_wrapper_event_rejects_unknown_name() -> None:
    with pytest.raises(WrapperEventParseError):
        parse_wrapper_event(
            'ATC_EVENT nope {"session_id":"sess_1","provider":"codex","command":"start-role"}'
        )


def test_parse_wrapper_event_requires_common_fields() -> None:
    with pytest.raises(WrapperEventParseError):
        parse_wrapper_event('ATC_EVENT runtime_ready {"session_id":"sess_1","provider":"codex"}')

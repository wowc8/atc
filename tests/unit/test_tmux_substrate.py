from __future__ import annotations

from atc.runtime.tmux.substrate import build_path_env_prefix, resolve_tmux_binary


def test_resolve_tmux_binary_returns_string() -> None:
    result = resolve_tmux_binary()
    assert isinstance(result, str)
    assert result


def test_build_path_env_prefix_returns_string() -> None:
    result = build_path_env_prefix(current_path="/usr/bin", home="/tmp/does-not-matter")
    assert isinstance(result, str)

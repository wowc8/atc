from __future__ import annotations

from atc.runtime.tmux.substrate import build_path_env_prefix, resolve_tmux_binary


def test_resolve_tmux_binary_returns_string() -> None:
    result = resolve_tmux_binary()
    assert isinstance(result, str)
    assert result


def test_build_path_env_prefix_returns_string() -> None:
    result = build_path_env_prefix(current_path="/usr/bin", home="/tmp/does-not-matter")
    assert isinstance(result, str)


def test_build_path_env_prefix_includes_user_local_bin(tmp_path) -> None:
    home = tmp_path / "home"
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)

    result = build_path_env_prefix(current_path="/usr/bin", home=str(home))

    assert result.startswith("PATH=")
    assert str(local_bin) in result
    assert result.endswith(" ")


def test_build_path_env_prefix_quotes_paths_with_spaces(tmp_path) -> None:
    home = tmp_path / "home with spaces"
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)

    result = build_path_env_prefix(current_path="/usr/bin", home=str(home))

    assert result.startswith("PATH=")
    assert "'" in result
    assert str(local_bin) in result

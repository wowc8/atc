from __future__ import annotations

from atc.providers.claude_code.runtime import ClaudeCodeRuntime
from atc.providers.codex.runtime import CodexRuntime
from atc.providers.registry import (
    ProviderRegistryError,
    create_provider_runtime,
    get_provider_runtime_factory,
    list_provider_runtime_infos,
    list_provider_runtimes,
    register_provider_runtime,
)


class DummyProvider:
    provider_name = "dummy"


class AnotherProvider:
    provider_name = "another"


def test_register_and_create_provider_runtime() -> None:
    register_provider_runtime("dummy_test", DummyProvider)

    provider = create_provider_runtime("dummy_test")

    assert isinstance(provider, DummyProvider)


def test_list_provider_runtimes_contains_registered_name() -> None:
    register_provider_runtime("another_test", AnotherProvider)

    assert "another_test" in list_provider_runtimes()


def test_create_provider_runtime_raises_for_unknown() -> None:
    try:
        create_provider_runtime("missing-provider")
    except ProviderRegistryError as exc:
        assert "Unknown provider runtime" in str(exc)
    else:
        raise AssertionError("Expected ProviderRegistryError")


def test_builtin_claude_runtime_registered() -> None:
    factory = get_provider_runtime_factory("claude_code")
    assert factory is ClaudeCodeRuntime


def test_builtin_codex_runtime_registered() -> None:
    factory = get_provider_runtime_factory("codex")
    assert factory is CodexRuntime



def test_list_provider_runtime_infos_contains_builtins() -> None:
    infos = list_provider_runtime_infos()
    names = {info.name for info in infos}
    assert "claude_code" in names
    assert "codex" in names



def test_list_provider_runtime_infos_exposes_builtin_metadata() -> None:
    infos = {info.name: info for info in list_provider_runtime_infos()}
    assert infos["claude_code"].supports_streaming is True
    assert infos["claude_code"].supports_tool_use is True
    assert infos["claude_code"].context_window == 200_000
    assert infos["claude_code"].model == "claude"
    assert infos["codex"].supports_streaming is True
    assert infos["codex"].supports_tool_use is True
    assert infos["codex"].context_window == 200_000
    assert infos["codex"].model == "codex"

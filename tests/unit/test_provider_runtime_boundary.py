from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REMOVED_LEGACY_PROVIDER_FILES = [
    "src/atc/agents/base.py",
    "src/atc/agents/factory.py",
    "src/atc/agents/codex_provider.py",
    "src/atc/agents/claude_provider.py",
    "src/atc/agents/opencode_provider.py",
    "src/atc/agents/plugins/_example_provider.py",
]

FORBIDDEN_ACTIVE_IMPORT_PARTS = [
    ("atc", "agents", "base"),
    ("atc", "agents", "factory"),
    ("atc", "agents", "codex_provider"),
    ("atc", "agents", "claude_provider"),
    ("atc", "agents", "opencode_provider"),
    ("atc", "agents", "codex_usage"),
    ("atc", "agents", "claude_runtime"),
]


def _python_files_under(*roots: str) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        files.extend((ROOT / root).rglob("*.py"))
    return sorted(files)


def test_legacy_agent_provider_files_removed() -> None:
    assert [rel for rel in REMOVED_LEGACY_PROVIDER_FILES if (ROOT / rel).exists()] == []


def test_no_active_python_imports_legacy_agent_provider_modules() -> None:
    offenders: list[str] = []
    for path in _python_files_under("src", "tests"):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        for parts in FORBIDDEN_ACTIVE_IMPORT_PARTS:
            forbidden = ".".join(parts)
            if forbidden in text:
                offenders.append(f"{path.relative_to(ROOT)} imports {forbidden}")
    assert offenders == []


def test_codex_usage_lives_under_codex_provider_package() -> None:
    assert (ROOT / "src/atc/providers/codex/usage.py").is_file()
    assert not (ROOT / "src/atc/agents/codex_usage.py").exists()


def test_claude_runtime_helpers_live_under_claude_provider_package() -> None:
    assert (ROOT / "src/atc/providers/claude_code/runtime_helpers.py").is_file()
    assert not (ROOT / "src/atc/agents/claude_runtime.py").exists()

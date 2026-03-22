"""Shared dataclasses for the ATC backup subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BackupResult:
    """Result from BackupService.create()."""

    path: Path
    size_bytes: int
    created_at: str
    entry_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class RestoreResult:
    """Result from BackupService.restore()."""

    projects_count: int
    sessions_count: int
    memories_count: int
    rebuilt_embeddings: int


@dataclass
class RemoteBackup:
    """A backup stored on a remote provider (Dropbox / Google Drive)."""

    remote_path: str
    name: str
    size_bytes: int
    created_at: str

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FileFingerprint:
    path: str
    is_dir: bool
    mtime_ns: int
    mtime: float
    size: int


@dataclass
class FileWatcher:
    watcher_id: str
    root: Path
    recursive: bool
    exclude: list[str]
    include_patterns: list[str]
    created_at: float
    last_polled_at: float
    snapshot: dict[str, FileFingerprint]
    native: Any | None = None
    events: list[dict] = field(default_factory=list)
    next_seq: int = 1
    dropped_until_seq: int = 0

from __future__ import annotations

import fnmatch
from pathlib import Path

from ... import security


def workspace_relative(path: Path) -> str:
    return path.resolve().relative_to(security.WORKSPACE).as_posix()


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)

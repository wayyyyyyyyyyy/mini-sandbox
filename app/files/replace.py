from pathlib import Path

from fastapi import HTTPException

from ..core.paths import relative_path
from ..security import ensure_file_size_allowed


def replace_file(
    *,
    path: Path,
    old_str: str,
    new_str: str,
    replace_all: bool,
    count: int | None,
    display_path: str,
) -> dict:
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {display_path}")

    content = path.read_text(encoding="utf-8")
    if old_str not in content:
        raise HTTPException(status_code=404, detail=f"old_str not found in file: {display_path}")

    match_count = content.count(old_str)
    replacement_limit = _replacement_limit(
        match_count=match_count,
        replace_all=replace_all,
        count=count,
    )
    replaced = min(match_count, replacement_limit)
    updated = content.replace(old_str, new_str, replacement_limit)
    updated_bytes = updated.encode("utf-8")
    ensure_file_size_allowed(updated_bytes)
    path.write_bytes(updated_bytes)

    return {
        "path": relative_path(path),
        "replaced": replaced,
        "changed": replaced > 0,
    }


def _replacement_limit(*, match_count: int, replace_all: bool, count: int | None) -> int:
    if count is not None:
        return count
    if replace_all:
        return match_count
    return 1

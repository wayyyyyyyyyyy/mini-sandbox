import re
from pathlib import Path

from fastapi import HTTPException

from ..core.paths import relative_path
from .helpers import matches_any


def search_file(
    *,
    path: Path,
    regex: str,
    case_insensitive: bool,
    max_results: int,
) -> dict:
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {relative_path(path)}")

    pattern = compile_regex(regex, case_insensitive)
    content = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    matches = []
    for line_number, line in enumerate(content.splitlines()):
        for match in pattern.finditer(line):
            matches.append(
                {
                    "line": line_number,
                    "text": line,
                    "match": match.group(0),
                }
            )
            if len(matches) >= max_results:
                return {"path": relative_path(path), "regex": regex, "matches": matches}

    return {"path": relative_path(path), "regex": regex, "matches": matches}


def grep_files(
    *,
    path: Path,
    pattern_text: str,
    include: list[str],
    exclude: list[str],
    case_insensitive: bool,
    max_results: int,
) -> dict:
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=404, detail=f"directory not found: {relative_path(path)}")

    pattern = compile_regex(pattern_text, case_insensitive)
    matches = []
    for child in sorted(path.rglob("*"), key=lambda item: relative_path(item)):
        if len(matches) >= max_results:
            break
        if not child.is_file():
            continue
        relative_text = child.relative_to(path).as_posix()
        if include and not matches_any(relative_text, include):
            continue
        if matches_any(relative_text, exclude):
            continue
        try:
            content = child.read_text(encoding="utf-8").replace("\r\n", "\n")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(content.splitlines()):
            for match in pattern.finditer(line):
                matches.append(
                    {
                        "path": relative_path(child),
                        "line": line_number,
                        "text": line,
                        "match": match.group(0),
                    }
                )
                if len(matches) >= max_results:
                    return {"path": relative_path(path), "pattern": pattern_text, "matches": matches}

    return {"path": relative_path(path), "pattern": pattern_text, "matches": matches}


def compile_regex(regex: str, case_insensitive: bool) -> re.Pattern[str]:
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        return re.compile(regex, flags)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"invalid regex: {exc}") from exc

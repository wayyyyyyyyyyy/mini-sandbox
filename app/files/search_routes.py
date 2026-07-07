import re

from fastapi import Depends, FastAPI, HTTPException

from ..auth import require_api_key
from ..core.paths import relative_path as _relative
from ..schemas import (
    FileFindRequest,
    FileFindResult,
    FileGlobRequest,
    FileGlobResult,
    FileGrepRequest,
    FileGrepResult,
    FileInfo,
    FileSearchRequest,
    FileSearchResult,
)
from ..security import resolve_workspace_path
from .helpers import is_hidden_relative, matches_any, size


def register_file_search_routes(app: FastAPI) -> None:
    @app.post("/file/find", response_model=FileFindResult)
    def file_find(request: FileFindRequest, _: None = Depends(require_api_key)) -> FileFindResult:
        path = resolve_workspace_path(request.path)
        if not path.exists() or not path.is_dir():
            raise HTTPException(status_code=404, detail=f"directory not found: {request.path}")

        files = []
        for child in sorted(path.rglob(request.glob), key=lambda item: _relative(item)):
            if len(files) >= request.max_results:
                break
            if not child.is_file():
                continue
            relative_to_root = child.relative_to(path)
            if not request.include_hidden and is_hidden_relative(relative_to_root):
                continue
            files.append(_relative(child))

        return FileFindResult(path=_relative(path), glob=request.glob, files=files)

    @app.post("/file/glob", response_model=FileGlobResult)
    def file_glob(request: FileGlobRequest, _: None = Depends(require_api_key)) -> FileGlobResult:
        path = resolve_workspace_path(request.path)
        if not path.exists() or not path.is_dir():
            raise HTTPException(status_code=404, detail=f"directory not found: {request.path}")

        candidates = list(path.glob(request.pattern))
        if request.sort_by == "name":
            candidates.sort(key=lambda item: item.name)
        else:
            candidates.sort(key=lambda item: _relative(item))

        matches = []
        entries = []
        for child in candidates:
            if len(matches) >= request.max_results:
                break
            if request.files_only and not child.is_file():
                continue
            relative_to_root = child.relative_to(path)
            relative_text = relative_to_root.as_posix()
            if not request.include_hidden and is_hidden_relative(relative_to_root):
                continue
            if matches_any(relative_text, request.exclude):
                continue
            matches.append(_relative(child))
            if request.include_metadata:
                if child.is_file():
                    kind = "file"
                elif child.is_dir():
                    kind = "directory"
                else:
                    kind = "other"
                entries.append(FileInfo(path=_relative(child), kind=kind, bytes=size(child)))

        return FileGlobResult(path=_relative(path), pattern=request.pattern, matches=matches, entries=entries)

    @app.post("/file/search", response_model=FileSearchResult)
    def file_search(request: FileSearchRequest, _: None = Depends(require_api_key)) -> FileSearchResult:
        path = resolve_workspace_path(request.path)
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail=f"file not found: {request.path}")

        pattern = _compile_regex(request.regex, request.case_insensitive)
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
                if len(matches) >= request.max_results:
                    return FileSearchResult(path=_relative(path), regex=request.regex, matches=matches)

        return FileSearchResult(path=_relative(path), regex=request.regex, matches=matches)

    @app.post("/file/grep", response_model=FileGrepResult)
    def file_grep(request: FileGrepRequest, _: None = Depends(require_api_key)) -> FileGrepResult:
        path = resolve_workspace_path(request.path)
        if not path.exists() or not path.is_dir():
            raise HTTPException(status_code=404, detail=f"directory not found: {request.path}")

        pattern = _compile_regex(request.pattern, request.case_insensitive)
        matches = []
        for child in sorted(path.rglob("*"), key=lambda item: _relative(item)):
            if len(matches) >= request.max_results:
                break
            if not child.is_file():
                continue
            relative_text = child.relative_to(path).as_posix()
            if request.include and not matches_any(relative_text, request.include):
                continue
            if matches_any(relative_text, request.exclude):
                continue
            try:
                content = child.read_text(encoding="utf-8").replace("\r\n", "\n")
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(content.splitlines()):
                for match in pattern.finditer(line):
                    matches.append(
                        {
                            "path": _relative(child),
                            "line": line_number,
                            "text": line,
                            "match": match.group(0),
                        }
                    )
                    if len(matches) >= request.max_results:
                        return FileGrepResult(path=_relative(path), pattern=request.pattern, matches=matches)

        return FileGrepResult(path=_relative(path), pattern=request.pattern, matches=matches)


def _compile_regex(regex: str, case_insensitive: bool) -> re.Pattern[str]:
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        return re.compile(regex, flags)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"invalid regex: {exc}") from exc

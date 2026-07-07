import asyncio
import base64
import fnmatch
import json
import re
import time
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from ..auth import require_api_key
from ..core.paths import relative_path as _relative
from ..file_watch import FileWatchManager
from ..schemas import (
    FileFindRequest,
    FileFindResult,
    FileGlobRequest,
    FileGlobResult,
    FileGrepRequest,
    FileGrepResult,
    FileInfo,
    FileListRequest,
    FileListResult,
    FileReadRequest,
    FileReadResult,
    FileReplaceRequest,
    FileReplaceResult,
    FileSearchRequest,
    FileSearchResult,
    FileWatchCreateRequest,
    FileWatchCreateResult,
    FileWatchDeleteResult,
    FileWatchPollRequest,
    FileWatchPollResult,
    FileWatchWaitRequest,
    FileWatchWaitResult,
    FileWriteRequest,
    FileWriteResult,
)
from ..security import ensure_file_size_allowed, resolve_workspace_path


def register_file_routes(app: FastAPI, file_watchers: FileWatchManager) -> None:
    @app.post("/file/read", response_model=FileReadResult)
    def file_read(request: FileReadRequest, _: None = Depends(require_api_key)) -> FileReadResult:
        path = resolve_workspace_path(request.path)
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail=f"file not found: {request.path}")

        content_bytes = path.read_bytes()
        ensure_file_size_allowed(content_bytes)
        content = content_bytes.decode("utf-8")
        line_count = None
        if request.start_line is not None or request.end_line is not None:
            content = content.replace("\r\n", "\n")
            lines = content.splitlines(keepends=True)
            start = request.start_line or 0
            end = request.end_line if request.end_line is not None else len(lines)
            if end < start:
                raise HTTPException(status_code=400, detail="end_line must be greater than or equal to start_line")
            selected = lines[start:end]
            content = "".join(selected)
            line_count = len(selected)

        return FileReadResult(
            path=_relative(path),
            content=content,
            bytes=len(content.encode("utf-8")),
            line_count=line_count,
        )

    @app.post("/file/write", response_model=FileWriteResult)
    def file_write(request: FileWriteRequest, _: None = Depends(require_api_key)) -> FileWriteResult:
        path = resolve_workspace_path(request.path)
        content_bytes = _file_content_bytes(request)
        ensure_file_size_allowed(content_bytes)

        if request.create_parent:
            path.parent.mkdir(parents=True, exist_ok=True)
        elif not path.parent.exists():
            raise HTTPException(status_code=400, detail=f"parent does not exist: {path.parent}")

        if request.append:
            with path.open("ab") as file:
                file.write(content_bytes)
        else:
            path.write_bytes(content_bytes)
        return FileWriteResult(path=_relative(path), bytes=path.stat().st_size)

    @app.post("/file/replace", response_model=FileReplaceResult)
    def file_replace(request: FileReplaceRequest, _: None = Depends(require_api_key)) -> FileReplaceResult:
        path = resolve_workspace_path(request.path)
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail=f"file not found: {request.path}")

        content = path.read_text(encoding="utf-8")
        if request.old_str not in content:
            raise HTTPException(status_code=404, detail=f"old_str not found in file: {request.path}")

        match_count = content.count(request.old_str)
        if request.count is not None:
            replacement_limit = request.count
        elif request.all:
            replacement_limit = match_count
        else:
            replacement_limit = 1

        replaced = min(match_count, replacement_limit)
        updated = content.replace(request.old_str, request.new_str, replacement_limit)
        ensure_file_size_allowed(updated.encode("utf-8"))
        path.write_bytes(updated.encode("utf-8"))

        return FileReplaceResult(path=_relative(path), replaced=replaced, changed=replaced > 0)

    @app.post("/file/watch", response_model=FileWatchCreateResult)
    def file_watch_create(
        request: FileWatchCreateRequest,
        _: None = Depends(require_api_key),
    ) -> FileWatchCreateResult:
        root = resolve_workspace_path(request.path)
        watcher = file_watchers.create(
            root=root,
            recursive=request.recursive,
            exclude=request.exclude,
            include_patterns=request.include_patterns,
        )
        return FileWatchCreateResult(
            watcher_id=watcher.watcher_id,
            path=_relative(root),
            recursive=watcher.recursive,
            cursor=0,
        )

    @app.post("/file/watch/wait", response_model=FileWatchWaitResult)
    def file_watch_wait(
        request: FileWatchWaitRequest,
        _: None = Depends(require_api_key),
    ) -> FileWatchWaitResult:
        path = resolve_workspace_path(request.path)
        return FileWatchWaitResult(**file_watchers.wait_for_file(
            path=path,
            timeout=request.timeout,
            event_types=request.event_types,
        ))

    @app.get("/file/watch/{watcher_id}/events")
    def file_watch_events(
        watcher_id: str,
        timeout: float = 30,
        heartbeat_interval: float = 15,
        last_event_id: str | None = None,
        last_event_id_header: str | None = Header(default=None, alias="Last-Event-ID"),
        _: None = Depends(require_api_key),
    ) -> StreamingResponse:
        file_watchers.ensure_exists(watcher_id)
        cursor = _file_watch_cursor_from_event_id(watcher_id, last_event_id or last_event_id_header)
        return StreamingResponse(
            _file_watch_sse_stream(
                file_watchers,
                watcher_id,
                cursor=cursor,
                timeout=max(0, min(timeout, 60)),
                heartbeat_interval=max(0.01, min(heartbeat_interval, 60)),
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @app.post("/file/watch/{watcher_id}/poll", response_model=FileWatchPollResult)
    def file_watch_poll(
        watcher_id: str,
        request: FileWatchPollRequest,
        _: None = Depends(require_api_key),
    ) -> FileWatchPollResult:
        return FileWatchPollResult(**file_watchers.poll(
            watcher_id,
            cursor=request.cursor,
            limit=request.limit,
            timeout=request.timeout,
        ))

    @app.delete("/file/watch/{watcher_id}", response_model=FileWatchDeleteResult)
    def file_watch_delete(watcher_id: str, _: None = Depends(require_api_key)) -> FileWatchDeleteResult:
        return FileWatchDeleteResult(**file_watchers.delete(watcher_id))

    @app.post("/file/upload", response_model=FileWriteResult)
    async def file_upload(
        path: str = Form(...),
        file: UploadFile = File(...),
        _: None = Depends(require_api_key),
    ) -> FileWriteResult:
        target = resolve_workspace_path(path)
        content = await file.read()
        ensure_file_size_allowed(content)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return FileWriteResult(path=_relative(target), bytes=target.stat().st_size)

    @app.get("/file/download")
    def file_download(path: str, _: None = Depends(require_api_key)) -> FileResponse:
        target = resolve_workspace_path(path)
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail=f"file not found: {path}")
        return FileResponse(path=target, filename=target.name, media_type="application/octet-stream")

    @app.post("/file/list", response_model=FileListResult)
    def file_list(request: FileListRequest, _: None = Depends(require_api_key)) -> FileListResult:
        path = resolve_workspace_path(request.path)
        if not path.exists() or not path.is_dir():
            raise HTTPException(status_code=404, detail=f"directory not found: {request.path}")

        entries = []
        children = path.rglob("*") if request.recursive else path.iterdir()
        for child in sorted(children, key=lambda item: str(item.relative_to(path))):
            if not request.show_hidden and _is_hidden_relative(child.relative_to(path)):
                continue
            if child.is_file():
                kind = "file"
            elif child.is_dir():
                kind = "directory"
            else:
                kind = "other"
            entries.append(
                FileInfo(
                    path=_relative(child),
                    kind=kind,
                    bytes=_size(child) if request.include_size else 0,
                )
            )

        return FileListResult(path=_relative(path), entries=entries)

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
            if not request.include_hidden and _is_hidden_relative(relative_to_root):
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
            if not request.include_hidden and _is_hidden_relative(relative_to_root):
                continue
            if _matches_any(relative_text, request.exclude):
                continue
            matches.append(_relative(child))
            if request.include_metadata:
                if child.is_file():
                    kind = "file"
                elif child.is_dir():
                    kind = "directory"
                else:
                    kind = "other"
                entries.append(FileInfo(path=_relative(child), kind=kind, bytes=_size(child)))

        return FileGlobResult(path=_relative(path), pattern=request.pattern, matches=matches, entries=entries)

    @app.post("/file/search", response_model=FileSearchResult)
    def file_search(request: FileSearchRequest, _: None = Depends(require_api_key)) -> FileSearchResult:
        path = resolve_workspace_path(request.path)
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail=f"file not found: {request.path}")

        flags = re.IGNORECASE if request.case_insensitive else 0
        try:
            pattern = re.compile(request.regex, flags)
        except re.error as exc:
            raise HTTPException(status_code=400, detail=f"invalid regex: {exc}") from exc

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

        flags = re.IGNORECASE if request.case_insensitive else 0
        try:
            pattern = re.compile(request.pattern, flags)
        except re.error as exc:
            raise HTTPException(status_code=400, detail=f"invalid regex: {exc}") from exc

        matches = []
        for child in sorted(path.rglob("*"), key=lambda item: _relative(item)):
            if len(matches) >= request.max_results:
                break
            if not child.is_file():
                continue
            relative_text = child.relative_to(path).as_posix()
            if request.include and not _matches_any(relative_text, request.include):
                continue
            if _matches_any(relative_text, request.exclude):
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


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return 0


def _file_content_bytes(request: FileWriteRequest) -> bytes:
    content = request.content
    if request.leading_newline:
        content = "\n" + content
    if request.trailing_newline:
        content = content + "\n"

    if request.encoding == "utf-8":
        return content.encode("utf-8")
    if request.encoding == "base64":
        try:
            return base64.b64decode(content, validate=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid base64 content") from exc
    if request.encoding == "raw":
        return content.encode("latin-1")
    raise HTTPException(status_code=400, detail=f"unsupported encoding: {request.encoding}")


def _is_hidden_relative(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


async def _file_watch_sse_stream(
    file_watchers: FileWatchManager,
    watcher_id: str,
    *,
    cursor: int,
    timeout: float,
    heartbeat_interval: float,
):
    yield _sse_message("watch_started", {"watcher_id": watcher_id, "cursor": cursor})
    deadline = time.monotonic() + timeout
    next_heartbeat = time.monotonic() + heartbeat_interval
    while time.monotonic() <= deadline:
        now = time.monotonic()
        wait_time = min(0.2, max(deadline - now, 0), max(next_heartbeat - now, 0))
        result = await asyncio.to_thread(
            file_watchers.poll,
            watcher_id,
            cursor=cursor,
            limit=100,
            timeout=wait_time,
        )
        cursor = result["cursor"]
        for event in result["events"]:
            yield _sse_message(
                "file_change",
                event,
                event_id=f"{watcher_id}:{event['seq']}",
            )
        if result["events"]:
            return
        if result["overflow"]:
            yield _sse_message("overflow", {"watcher_id": watcher_id, "cursor": cursor})
            return
        if time.monotonic() >= next_heartbeat:
            yield _sse_message("heartbeat", {"watcher_id": watcher_id, "cursor": cursor})
            next_heartbeat = time.monotonic() + heartbeat_interval
        await asyncio.sleep(0)


def _file_watch_cursor_from_event_id(watcher_id: str, event_id: str | None) -> int:
    if not event_id:
        return 0
    prefix = f"{watcher_id}:"
    if not event_id.startswith(prefix):
        raise HTTPException(status_code=400, detail="Last-Event-ID watcher_id mismatch")
    try:
        return int(event_id.removeprefix(prefix))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid Last-Event-ID cursor") from exc


def _sse_message(event: str, data: dict, event_id: str | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"

import asyncio
import json
import time

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from ..auth import require_api_key
from ..core.paths import relative_path as _relative
from ..schemas import (
    FileWatchCreateRequest,
    FileWatchCreateResult,
    FileWatchDeleteResult,
    FileWatchPollRequest,
    FileWatchPollResult,
    FileWatchWaitRequest,
    FileWatchWaitResult,
)
from ..security import resolve_workspace_path
from .watch import FileWatchManager


def register_file_watch_routes(app: FastAPI, file_watchers: FileWatchManager) -> None:
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

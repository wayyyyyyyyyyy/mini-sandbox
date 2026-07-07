from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

from fastapi import HTTPException

from ...config import MAX_FILE_WATCHERS
from .backends import create_native_file_waiter, create_native_watcher
from .history import trim_history
from .models import FileWatcher
from .snapshot import (
    diff_snapshot,
    fingerprint_file,
    scan_snapshot,
    wait_event,
    wait_event_type,
)


class FileWatchManager:
    def __init__(self, *, max_watchers: int | None = None) -> None:
        self._watchers: dict[str, FileWatcher] = {}
        self._lock = threading.Lock()
        self.max_watchers = MAX_FILE_WATCHERS if max_watchers is None else max_watchers

    def create(
        self,
        *,
        root: Path,
        recursive: bool,
        exclude: list[str],
        include_patterns: list[str],
    ) -> FileWatcher:
        if not root.exists() or not root.is_dir():
            raise HTTPException(status_code=404, detail=f"directory not found: {root}")

        now = time.time()
        watcher = FileWatcher(
            watcher_id=f"fw_{uuid.uuid4().hex}",
            root=root,
            recursive=recursive,
            exclude=exclude,
            include_patterns=include_patterns,
            created_at=now,
            last_polled_at=now,
            snapshot=scan_snapshot(root, recursive, exclude, include_patterns),
            native=create_native_watcher(root, recursive),
        )
        with self._lock:
            if self.max_watchers > 0 and len(self._watchers) >= self.max_watchers:
                raise HTTPException(status_code=429, detail="file watcher limit exceeded")
            self._watchers[watcher.watcher_id] = watcher
        return watcher

    def poll(self, watcher_id: str, *, cursor: int, limit: int, timeout: float = 0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            result = self._poll_once(watcher_id, cursor=cursor, limit=limit)
            if result["events"] or timeout <= 0 or time.monotonic() >= deadline:
                return result
            time.sleep(min(0.05, max(deadline - time.monotonic(), 0)))

    def wait_for_file(self, *, path: Path, timeout: float, event_types: list[str]) -> dict:
        previous = fingerprint_file(path)
        if previous is not None and "create" in event_types:
            return {"event": wait_event(1, "create", path, previous)}

        native = create_native_file_waiter(path)
        if native is not None:
            try:
                return native.wait(previous=previous, timeout=timeout, event_types=event_types)
            finally:
                native.close()

        deadline = time.monotonic() + timeout
        seq = 1
        while True:
            current = fingerprint_file(path)
            event_type = wait_event_type(previous, current, event_types)
            if event_type is not None:
                fingerprint = current if current is not None else previous
                return {"event": wait_event(seq, event_type, path, fingerprint, deleted=current is None)}
            if time.monotonic() >= deadline:
                return {"event": None}
            previous = current
            time.sleep(min(0.05, max(deadline - time.monotonic(), 0)))

    def ensure_exists(self, watcher_id: str) -> None:
        self._get(watcher_id)

    def delete(self, watcher_id: str) -> dict:
        with self._lock:
            watcher = self._watchers.pop(watcher_id, None)
        if watcher is None:
            raise HTTPException(status_code=404, detail=f"file watcher not found: {watcher_id}")
        if watcher.native is not None:
            watcher.native.close()
        return {"watcher_id": watcher_id, "closed": True}

    def _get(self, watcher_id: str) -> FileWatcher:
        with self._lock:
            watcher = self._watchers.get(watcher_id)
        if watcher is None:
            raise HTTPException(status_code=404, detail=f"file watcher not found: {watcher_id}")
        return watcher

    def _poll_once(self, watcher_id: str, *, cursor: int, limit: int) -> dict:
        watcher = self._get(watcher_id)
        with self._lock:
            if watcher.native is not None:
                new_events = watcher.native.read_events(watcher)
            else:
                current = scan_snapshot(watcher.root, watcher.recursive, watcher.exclude, watcher.include_patterns)
                new_events = diff_snapshot(watcher, current)
                watcher.snapshot = current
            watcher.last_polled_at = time.time()
            watcher.events.extend(new_events)
            trim_history(watcher)
            events = [event for event in watcher.events if event["seq"] > cursor]
            limited = events[:limit]
            next_cursor = limited[-1]["seq"] if limited else cursor
            max_cursor = watcher.events[-1]["seq"] if watcher.events else 0
            overflow = bool(watcher.dropped_until_seq and cursor < watcher.dropped_until_seq)
            return {
                "watcher_id": watcher.watcher_id,
                "cursor": max(next_cursor, min(cursor, max_cursor)),
                "events": limited,
                "overflow": overflow,
            }

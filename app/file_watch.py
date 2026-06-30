import fnmatch
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException

from . import security


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
    events: list[dict] = field(default_factory=list)
    next_seq: int = 1


class FileWatchManager:
    def __init__(self) -> None:
        self._watchers: dict[str, FileWatcher] = {}
        self._lock = threading.Lock()

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
            snapshot=self._scan(root, recursive, exclude, include_patterns),
        )
        with self._lock:
            self._watchers[watcher.watcher_id] = watcher
        return watcher

    def poll(self, watcher_id: str, *, cursor: int, limit: int) -> dict:
        watcher = self._get(watcher_id)
        with self._lock:
            current = self._scan(watcher.root, watcher.recursive, watcher.exclude, watcher.include_patterns)
            new_events = self._diff(watcher, current)
            watcher.snapshot = current
            watcher.last_polled_at = time.time()
            watcher.events.extend(new_events)
            events = [event for event in watcher.events if event["seq"] > cursor]
            limited = events[:limit]
            next_cursor = limited[-1]["seq"] if limited else cursor
            max_cursor = watcher.events[-1]["seq"] if watcher.events else 0
            return {
                "watcher_id": watcher.watcher_id,
                "cursor": max(next_cursor, min(cursor, max_cursor)),
                "events": limited,
                "overflow": False,
            }

    def delete(self, watcher_id: str) -> dict:
        with self._lock:
            watcher = self._watchers.pop(watcher_id, None)
        if watcher is None:
            raise HTTPException(status_code=404, detail=f"file watcher not found: {watcher_id}")
        return {"watcher_id": watcher_id, "closed": True}

    def _get(self, watcher_id: str) -> FileWatcher:
        with self._lock:
            watcher = self._watchers.get(watcher_id)
        if watcher is None:
            raise HTTPException(status_code=404, detail=f"file watcher not found: {watcher_id}")
        return watcher

    def _diff(self, watcher: FileWatcher, current: dict[str, FileFingerprint]) -> list[dict]:
        events = []
        previous = watcher.snapshot
        now = time.time()

        for path in sorted(current.keys() - previous.keys()):
            events.append(self._event(watcher, "created", current[path], now))

        for path in sorted(current.keys() & previous.keys()):
            old = previous[path]
            new = current[path]
            if old.mtime_ns != new.mtime_ns or old.size != new.size or old.is_dir != new.is_dir:
                events.append(self._event(watcher, "modified", new, now))

        for path in sorted(previous.keys() - current.keys()):
            events.append(self._event(watcher, "deleted", previous[path], now, deleted=True))

        return events

    def _event(
        self,
        watcher: FileWatcher,
        event_type: str,
        fingerprint: FileFingerprint,
        timestamp: float,
        *,
        deleted: bool = False,
    ) -> dict:
        seq = watcher.next_seq
        watcher.next_seq += 1
        return {
            "seq": seq,
            "type": event_type,
            "path": _workspace_relative(watcher.root / fingerprint.path),
            "relative_path": fingerprint.path,
            "is_dir": fingerprint.is_dir,
            "timestamp": timestamp,
            "mtime": None if deleted else fingerprint.mtime,
            "size": 0 if deleted else fingerprint.size,
        }

    def _scan(
        self,
        root: Path,
        recursive: bool,
        exclude: list[str],
        include_patterns: list[str],
    ) -> dict[str, FileFingerprint]:
        candidates = root.rglob("*") if recursive else root.iterdir()
        snapshot = {}
        for child in sorted(candidates, key=lambda item: item.relative_to(root).as_posix()):
            relative = child.relative_to(root).as_posix()
            if _matches_any(relative, exclude):
                continue
            if include_patterns and not child.is_dir() and not _matches_any(relative, include_patterns):
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            snapshot[relative] = FileFingerprint(
                path=relative,
                is_dir=child.is_dir(),
                mtime_ns=stat.st_mtime_ns,
                mtime=stat.st_mtime,
                size=stat.st_size if child.is_file() else 0,
            )
        return snapshot


def _workspace_relative(path: Path) -> str:
    return path.resolve().relative_to(security.WORKSPACE).as_posix()


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)

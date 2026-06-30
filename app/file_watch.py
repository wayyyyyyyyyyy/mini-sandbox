import fnmatch
import ctypes
import os
import platform
import select
import struct
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from . import security
from .config import MAX_FILE_WATCHERS


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
            snapshot=self._scan(root, recursive, exclude, include_patterns),
            native=_create_native_watcher(root, recursive),
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
        previous = _fingerprint_file(path)
        if previous is not None and "create" in event_types:
            return {"event": _wait_event(1, "create", path, previous)}

        native = _create_native_file_waiter(path)
        if native is not None:
            try:
                return native.wait(previous=previous, timeout=timeout, event_types=event_types)
            finally:
                native.close()

        deadline = time.monotonic() + timeout
        seq = 1
        while True:
            current = _fingerprint_file(path)
            event_type = _wait_event_type(previous, current, event_types)
            if event_type is not None:
                fingerprint = current if current is not None else previous
                return {"event": _wait_event(seq, event_type, path, fingerprint, deleted=current is None)}
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


IN_ACCESS = 0x00000001
IN_MODIFY = 0x00000002
IN_ATTRIB = 0x00000004
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_ISDIR = 0x40000000
IN_NONBLOCK = 0x00000800
IN_CLOEXEC = 0x00080000
IN_EVENT_STRUCT = struct.Struct("iIII")
IN_WATCH_MASK = (
    IN_MODIFY
    | IN_ATTRIB
    | IN_CLOSE_WRITE
    | IN_MOVED_FROM
    | IN_MOVED_TO
    | IN_CREATE
    | IN_DELETE
    | IN_DELETE_SELF
    | IN_MOVE_SELF
)


class LinuxInotifyWatcher:
    def __init__(self, *, root: Path, recursive: bool) -> None:
        self.root = root
        self.recursive = recursive
        self.fd = _inotify_init()
        self._wd_to_path: dict[int, Path] = {}
        self._path_to_wd: dict[Path, int] = {}
        self._add_existing(root)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def read_events(self, watcher: FileWatcher) -> list[dict]:
        candidates = []
        while True:
            try:
                readable, _, _ = select.select([self.fd], [], [], 0)
                if not readable:
                    return self._number_events(watcher, _coalesce_native_candidates(candidates))
                chunk = os.read(self.fd, 65536)
            except BlockingIOError:
                return self._number_events(watcher, _coalesce_native_candidates(candidates))
            if not chunk:
                return self._number_events(watcher, _coalesce_native_candidates(candidates))
            candidates.extend(self._parse_events(watcher, chunk))

    def _add_existing(self, root: Path) -> None:
        self._add_watch(root)
        if not self.recursive:
            return
        for child in sorted(root.rglob("*")):
            if child.is_dir():
                self._add_watch(child)

    def _add_watch(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved in self._path_to_wd:
            return
        wd = _inotify_add_watch(self.fd, resolved, IN_WATCH_MASK)
        self._wd_to_path[wd] = resolved
        self._path_to_wd[resolved] = wd

    def _parse_events(self, watcher: FileWatcher, chunk: bytes) -> list[dict]:
        offset = 0
        events = []
        while offset + IN_EVENT_STRUCT.size <= len(chunk):
            wd, mask, _cookie, name_len = IN_EVENT_STRUCT.unpack_from(chunk, offset)
            offset += IN_EVENT_STRUCT.size
            raw_name = chunk[offset : offset + name_len].split(b"\0", 1)[0]
            offset += name_len
            parent = self._wd_to_path.get(wd, watcher.root)
            path = parent / raw_name.decode("utf-8", errors="replace") if raw_name else parent
            if self.recursive and mask & IN_ISDIR and mask & (IN_CREATE | IN_MOVED_TO):
                try:
                    self._add_watch(path)
                except OSError:
                    pass
            event_type = _inotify_event_type(mask)
            if event_type is None:
                continue
            relative = path.resolve().relative_to(watcher.root).as_posix()
            if _matches_any(relative, watcher.exclude):
                continue
            if watcher.include_patterns and not path.is_dir() and not _matches_any(relative, watcher.include_patterns):
                continue
            fingerprint = _fingerprint_native(path, relative, bool(mask & IN_ISDIR))
            events.append({
                "type": event_type,
                "path": _workspace_relative(path),
                "relative_path": relative,
                "is_dir": fingerprint.is_dir,
                "timestamp": time.time(),
                "mtime": None if event_type == "deleted" else fingerprint.mtime,
                "size": 0 if event_type == "deleted" else fingerprint.size,
            })
        return events

    def _number_events(self, watcher: FileWatcher, events: list[dict]) -> list[dict]:
        numbered = []
        for event in events:
            numbered.append({"seq": watcher.next_seq, **event})
            watcher.next_seq += 1
        return numbered


class LinuxInotifyFileWaiter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.parent = path.parent
        self.fd = _inotify_init()
        self.wd = _inotify_add_watch(self.fd, self.parent, IN_WATCH_MASK)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def wait(self, *, previous: FileFingerprint | None, timeout: float, event_types: list[str]) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = max(deadline - time.monotonic(), 0)
            if remaining == 0:
                return {"event": None}
            readable, _, _ = select.select([self.fd], [], [], remaining)
            if not readable:
                return {"event": None}
            try:
                chunk = os.read(self.fd, 65536)
            except BlockingIOError:
                continue
            event = self._event_from_chunk(chunk, previous, event_types)
            if event is not None:
                return {"event": event}
            previous = _fingerprint_file(self.path)

    def _event_from_chunk(
        self,
        chunk: bytes,
        previous: FileFingerprint | None,
        event_types: list[str],
    ) -> dict | None:
        offset = 0
        while offset + IN_EVENT_STRUCT.size <= len(chunk):
            _wd, mask, _cookie, name_len = IN_EVENT_STRUCT.unpack_from(chunk, offset)
            offset += IN_EVENT_STRUCT.size
            raw_name = chunk[offset : offset + name_len].split(b"\0", 1)[0]
            offset += name_len
            if raw_name.decode("utf-8", errors="replace") != self.path.name:
                continue
            current = _fingerprint_file(self.path)
            event_type = _inotify_wait_event_type(mask, previous, current, event_types)
            if event_type is not None:
                fingerprint = current if current is not None else previous
                return _wait_event(1, event_type, self.path, fingerprint, deleted=current is None)
        return None


def _workspace_relative(path: Path) -> str:
    return path.resolve().relative_to(security.WORKSPACE).as_posix()


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _inotify_init() -> int:
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    fd = libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
    if fd < 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))
    return fd


def _inotify_add_watch(fd: int, path: Path, mask: int) -> int:
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    wd = libc.inotify_add_watch(fd, os.fsencode(path), mask)
    if wd < 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))
    return wd


def _create_native_watcher(root: Path, recursive: bool):
    if platform.system().lower() != "linux":
        return None
    try:
        return LinuxInotifyWatcher(root=root, recursive=recursive)
    except OSError:
        return None


def _create_native_file_waiter(path: Path):
    if platform.system().lower() != "linux":
        return None
    try:
        return LinuxInotifyFileWaiter(path)
    except OSError:
        return None


def _fingerprint_file(path: Path) -> FileFingerprint | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    return FileFingerprint(
        path=path.name,
        is_dir=path.is_dir(),
        mtime_ns=stat.st_mtime_ns,
        mtime=stat.st_mtime,
        size=stat.st_size if path.is_file() else 0,
    )


def _fingerprint_native(path: Path, relative: str, is_dir_hint: bool) -> FileFingerprint:
    try:
        stat = path.stat()
    except OSError:
        return FileFingerprint(
            path=relative,
            is_dir=is_dir_hint,
            mtime_ns=0,
            mtime=0,
            size=0,
        )
    return FileFingerprint(
        path=relative,
        is_dir=path.is_dir(),
        mtime_ns=stat.st_mtime_ns,
        mtime=stat.st_mtime,
        size=stat.st_size if path.is_file() else 0,
    )


def _inotify_event_type(mask: int) -> str | None:
    if mask & (IN_CREATE | IN_MOVED_TO):
        return "created"
    if mask & (IN_DELETE | IN_DELETE_SELF | IN_MOVED_FROM | IN_MOVE_SELF):
        return "deleted"
    if mask & (IN_MODIFY | IN_CLOSE_WRITE | IN_ATTRIB):
        return "modified"
    return None


def _coalesce_native_candidates(events: list[dict]) -> list[dict]:
    by_path: dict[str, dict] = {}
    order = []
    priority = {"deleted": 3, "created": 2, "modified": 1}
    for event in events:
        key = event["path"]
        if key not in by_path:
            by_path[key] = event
            order.append(key)
            continue
        current = by_path[key]
        if priority[event["type"]] >= priority[current["type"]]:
            by_path[key] = event
        elif current["type"] == "created" and event["type"] == "modified":
            current["mtime"] = event["mtime"]
            current["size"] = event["size"]
            current["timestamp"] = event["timestamp"]
    return [by_path[key] for key in order]


def _inotify_wait_event_type(
    mask: int,
    previous: FileFingerprint | None,
    current: FileFingerprint | None,
    event_types: list[str],
) -> str | None:
    if mask & (IN_CREATE | IN_MOVED_TO) and "create" in event_types:
        return "create"
    if mask & (IN_DELETE | IN_DELETE_SELF | IN_MOVED_FROM | IN_MOVE_SELF) and "remove" in event_types:
        return "remove"
    if mask & (IN_MODIFY | IN_CLOSE_WRITE | IN_ATTRIB) and "write" in event_types:
        if previous is None or current is None:
            return "write"
        if (
            previous.mtime_ns != current.mtime_ns
            or previous.size != current.size
            or previous.is_dir != current.is_dir
        ):
            return "write"
    return _wait_event_type(previous, current, event_types)


def _wait_event_type(
    previous: FileFingerprint | None,
    current: FileFingerprint | None,
    event_types: list[str],
) -> str | None:
    if previous is None and current is not None and "create" in event_types:
        return "create"
    if previous is not None and current is None and "remove" in event_types:
        return "remove"
    if previous is not None and current is not None and "write" in event_types:
        if (
            previous.mtime_ns != current.mtime_ns
            or previous.size != current.size
            or previous.is_dir != current.is_dir
        ):
            return "write"
    return None


def _wait_event(
    seq: int,
    event_type: str,
    path: Path,
    fingerprint: FileFingerprint | None,
    *,
    deleted: bool = False,
) -> dict:
    now = time.time()
    return {
        "seq": seq,
        "type": event_type,
        "path": _workspace_relative(path),
        "relative_path": path.name,
        "is_dir": False if fingerprint is None else fingerprint.is_dir,
        "timestamp": now,
        "mtime": None if deleted or fingerprint is None else fingerprint.mtime,
        "size": 0 if deleted or fingerprint is None else fingerprint.size,
    }

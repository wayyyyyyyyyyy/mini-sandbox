from __future__ import annotations

import ctypes
import os
import platform
import select
import struct
import time
from pathlib import Path

from ..models import FileFingerprint, FileWatcher
from ..snapshot import (
    fingerprint_file,
    fingerprint_native,
    stable_fingerprint_file,
    wait_event,
    wait_event_type,
)
from ..utils import matches_any, workspace_relative

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
            if matches_any(relative, watcher.exclude):
                continue
            if watcher.include_patterns and not path.is_dir() and not matches_any(relative, watcher.include_patterns):
                continue
            fingerprint = fingerprint_native(path, relative, bool(mask & IN_ISDIR))
            events.append({
                "type": event_type,
                "path": workspace_relative(path),
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
            previous = fingerprint_file(self.path)

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
            current = fingerprint_file(self.path)
            event_type = _inotify_wait_event_type(mask, previous, current, event_types)
            if event_type is not None:
                if event_type == "write":
                    current = stable_fingerprint_file(self.path, current)
                fingerprint = current if current is not None else previous
                return wait_event(1, event_type, self.path, fingerprint, deleted=current is None)
        return None


def create_native_watcher(root: Path, recursive: bool):
    if platform.system().lower() != "linux":
        return None
    try:
        return LinuxInotifyWatcher(root=root, recursive=recursive)
    except OSError:
        return None


def create_native_file_waiter(path: Path):
    if platform.system().lower() != "linux":
        return None
    try:
        return LinuxInotifyFileWaiter(path)
    except OSError:
        return None


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
    return wait_event_type(previous, current, event_types)

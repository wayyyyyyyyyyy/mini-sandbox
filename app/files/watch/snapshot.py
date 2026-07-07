from __future__ import annotations

import time
from pathlib import Path

from .models import FileFingerprint, FileWatcher
from .utils import matches_any, workspace_relative


def scan_snapshot(
    root: Path,
    recursive: bool,
    exclude: list[str],
    include_patterns: list[str],
) -> dict[str, FileFingerprint]:
    candidates = root.rglob("*") if recursive else root.iterdir()
    snapshot = {}
    for child in sorted(candidates, key=lambda item: item.relative_to(root).as_posix()):
        relative = child.relative_to(root).as_posix()
        if matches_any(relative, exclude):
            continue
        if include_patterns and not child.is_dir() and not matches_any(relative, include_patterns):
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


def diff_snapshot(watcher: FileWatcher, current: dict[str, FileFingerprint]) -> list[dict]:
    events = []
    previous = watcher.snapshot
    now = time.time()

    for path in sorted(current.keys() - previous.keys()):
        events.append(watch_event(watcher, "created", current[path], now))

    for path in sorted(current.keys() & previous.keys()):
        old = previous[path]
        new = current[path]
        if old.mtime_ns != new.mtime_ns or old.size != new.size or old.is_dir != new.is_dir:
            events.append(watch_event(watcher, "modified", new, now))

    for path in sorted(previous.keys() - current.keys()):
        events.append(watch_event(watcher, "deleted", previous[path], now, deleted=True))

    return events


def watch_event(
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
        "path": workspace_relative(watcher.root / fingerprint.path),
        "relative_path": fingerprint.path,
        "is_dir": fingerprint.is_dir,
        "timestamp": timestamp,
        "mtime": None if deleted else fingerprint.mtime,
        "size": 0 if deleted else fingerprint.size,
    }


def fingerprint_file(path: Path) -> FileFingerprint | None:
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


def stable_fingerprint_file(
    path: Path,
    current: FileFingerprint | None,
    *,
    attempts: int = 4,
    interval: float = 0.02,
) -> FileFingerprint | None:
    previous = current
    for _ in range(attempts):
        time.sleep(interval)
        next_fingerprint = fingerprint_file(path)
        if next_fingerprint == previous:
            return next_fingerprint
        previous = next_fingerprint
    return previous


def fingerprint_native(path: Path, relative: str, is_dir_hint: bool) -> FileFingerprint:
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


def wait_event_type(
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


def wait_event(
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
        "path": workspace_relative(path),
        "relative_path": path.name,
        "is_dir": False if fingerprint is None else fingerprint.is_dir,
        "timestamp": now,
        "mtime": None if deleted or fingerprint is None else fingerprint.mtime,
        "size": 0 if deleted or fingerprint is None else fingerprint.size,
    }

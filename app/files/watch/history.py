from __future__ import annotations

import sys

from ...config import MAX_FILE_WATCH_EVENTS
from .models import FileWatcher


def trim_history(watcher: FileWatcher) -> None:
    max_events = max_file_watch_events()
    if max_events <= 0:
        if watcher.events:
            watcher.dropped_until_seq = watcher.events[-1]["seq"]
            watcher.events = []
        return
    overflow_count = len(watcher.events) - max_events
    if overflow_count <= 0:
        return
    dropped = watcher.events[:overflow_count]
    watcher.events = watcher.events[overflow_count:]
    watcher.dropped_until_seq = dropped[-1]["seq"]


def max_file_watch_events() -> int:
    compatibility_module = sys.modules.get("app.file_watch")
    if compatibility_module is not None:
        return int(getattr(compatibility_module, "MAX_FILE_WATCH_EVENTS", MAX_FILE_WATCH_EVENTS))
    return MAX_FILE_WATCH_EVENTS

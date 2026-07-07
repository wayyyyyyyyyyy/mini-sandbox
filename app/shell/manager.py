from __future__ import annotations

import threading

from .execution import ShellExecutionMixin
from .models import ShellSession
from .registry import ShellSessionRegistryMixin, default_idle_timeout, default_max_sessions


class ShellSessionManager(ShellSessionRegistryMixin, ShellExecutionMixin):
    def __init__(self, *, max_sessions: int | None = None, idle_timeout_seconds: int | None = None) -> None:
        self._sessions: dict[str, ShellSession] = {}
        self._lock = threading.Lock()
        self.max_sessions = default_max_sessions(max_sessions)
        self.idle_timeout_seconds = default_idle_timeout(idle_timeout_seconds)

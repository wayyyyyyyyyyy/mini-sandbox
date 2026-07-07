from __future__ import annotations

import threading

from .execution import BashExecutionMixin
from .models import BashSession
from .registry import BashSessionRegistryMixin, default_max_sessions


class BashSessionManager(BashSessionRegistryMixin, BashExecutionMixin):
    def __init__(self, *, max_sessions: int | None = None) -> None:
        self._sessions: dict[str, BashSession] = {}
        self._lock = threading.Lock()
        self.max_sessions = default_max_sessions(max_sessions)

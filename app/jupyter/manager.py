from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any

from .config import JUPYTER_SESSION_TIMEOUT_SECONDS, MAX_JUPYTER_SESSIONS
from .execution import execute_in_session
from .formatters import session_info
from .kernels import KernelCatalog
from .models import JupyterSession
from .registry import JupyterSessionRegistryMixin


class JupyterSessionManager(JupyterSessionRegistryMixin):
    def __init__(
        self,
        *,
        max_sessions: int = MAX_JUPYTER_SESSIONS,
        session_timeout_seconds: int = JUPYTER_SESSION_TIMEOUT_SECONDS,
    ) -> None:
        self.max_sessions = max_sessions
        self.session_timeout_seconds = session_timeout_seconds
        self._sessions: dict[str, JupyterSession] = {}
        self._lock = Lock()
        self._kernels = KernelCatalog()

    def execute(
        self,
        *,
        code: str,
        timeout: int,
        session_id: str | None = None,
        kernel_name: str | None = None,
        cwd: Path | None = None,
    ) -> dict[str, Any]:
        session = self.create_session(session_id=session_id, kernel_name=kernel_name, cwd=cwd)
        return execute_in_session(session, code=code, timeout=timeout)

    def session_info(self, session: JupyterSession) -> dict[str, Any]:
        return session_info(session)

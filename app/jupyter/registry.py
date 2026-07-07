from __future__ import annotations

import uuid
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import HTTPException

from .. import security
from .kernels import KernelCatalog
from .lifecycle import shutdown_session, start_session
from .models import JupyterSession, utcnow


class JupyterSessionRegistryMixin:
    max_sessions: int
    session_timeout_seconds: int
    _sessions: dict[str, JupyterSession]
    _lock: Lock
    _kernels: KernelCatalog

    def info(self) -> dict[str, Any]:
        return {
            "default_kernel": self.default_kernel(),
            "available_kernels": self.available_kernels(),
            "active_sessions": len(self.list()),
            "session_timeout_seconds": self.session_timeout_seconds,
            "max_sessions": self.max_sessions,
            "description": "Jupyter kernel execution service",
            "kernel_detection": "jupyter_client KernelSpecManager",
        }

    def default_kernel(self) -> str:
        return self._kernels.default_kernel()

    def available_kernels(self) -> list[str]:
        return self._kernels.available_kernels()

    def create_session(
        self,
        *,
        session_id: str | None = None,
        kernel_name: str | None = None,
        cwd: Path | None = None,
    ) -> JupyterSession:
        session_id = session_id or f"jp_{uuid.uuid4().hex}"
        kernel_name = kernel_name or self.default_kernel()
        cwd = cwd or security.WORKSPACE
        now = utcnow()
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing
            if self.max_sessions > 0 and len(self._sessions) >= self.max_sessions:
                raise HTTPException(status_code=429, detail="jupyter session limit exceeded")
            if kernel_name not in self.available_kernels():
                raise HTTPException(status_code=404, detail=f"jupyter kernel not found: {kernel_name}")
            session = start_session(
                session_id=session_id,
                kernel_name=kernel_name,
                cwd=cwd,
                now=now,
            )
            self._sessions[session_id] = session
            return session

    def list(self) -> dict[str, JupyterSession]:
        with self._lock:
            return dict(self._sessions)

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            raise HTTPException(status_code=404, detail=f"jupyter session not found: {session_id}")
        shutdown_session(session)

    def delete_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            shutdown_session(session)

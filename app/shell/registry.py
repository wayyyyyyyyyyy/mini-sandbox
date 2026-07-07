from __future__ import annotations

import threading
import uuid
from pathlib import Path

from fastapi import HTTPException

from ..config import MAX_SHELL_SESSIONS, SHELL_SESSION_IDLE_TIMEOUT_SECONDS
from .models import ShellSession, utcnow
from .runtime_config import workspace


class ShellSessionRegistryMixin:
    _sessions: dict[str, ShellSession]
    _lock: threading.Lock
    max_sessions: int
    idle_timeout_seconds: int

    def create_session(self, *, session_id: str | None = None, exec_dir: Path | None = None) -> ShellSession:
        session_id = session_id or f"sh_{uuid.uuid4().hex}"
        now = utcnow()
        root = exec_dir or workspace()
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing
            if self.max_sessions > 0 and len(self._sessions) >= self.max_sessions:
                raise HTTPException(status_code=429, detail="shell session limit exceeded")
            session = ShellSession(
                session_id=session_id,
                working_dir=root,
                workspace=root.resolve(),
                created_at=now,
                last_used_at=now,
            )
            self._sessions[session_id] = session
            return session

    def get(self, session_id: str) -> ShellSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"shell session not found: {session_id}")
        return session

    def list(self) -> dict[str, ShellSession]:
        with self._lock:
            return dict(self._sessions)

    def update_session(self, *, session_id: str, no_change_timeout: int | None) -> ShellSession:
        session = self.get(session_id)
        with session.output_changed:
            session.no_change_timeout = no_change_timeout
            session.last_used_at = utcnow()
            session.output_changed.notify_all()
        return session

    def stats(self) -> dict[str, int | float]:
        sessions = self.list()
        total_sessions = len(sessions)
        active_sessions = 0
        for session in sessions.values():
            if session.command_status == "running":
                active_sessions += 1
        idle_sessions = total_sessions - active_sessions
        usage_ratio = total_sessions / self.max_sessions if self.max_sessions > 0 else 0
        return {
            "total_sessions": total_sessions,
            "active_sessions": active_sessions,
            "idle_sessions": idle_sessions,
            "max_sessions": self.max_sessions,
            "session_timeout": self.idle_timeout_seconds,
            "usage_ratio": usage_ratio,
        }

    def cleanup_idle_sessions(self) -> list[str]:
        if self.idle_timeout_seconds <= 0:
            return []
        now = utcnow()
        with self._lock:
            idle_ids = [
                session_id
                for session_id, session in self._sessions.items()
                if session.current_process is None or session.current_process.poll() is not None
                if (now - session.last_used_at).total_seconds() >= self.idle_timeout_seconds
            ]
        for session_id in idle_ids:
            self.close(session_id)
            with self._lock:
                self._sessions.pop(session_id, None)
        return idle_ids

    def close(self, session_id: str) -> ShellSession:
        session = self.get(session_id)
        self.kill(session_id)
        with session.output_changed:
            session.status = "closed"
            session.killed = False
            session.last_used_at = utcnow()
            session.output_changed.notify_all()
        return session


def default_max_sessions(max_sessions: int | None) -> int:
    return MAX_SHELL_SESSIONS if max_sessions is None else max_sessions


def default_idle_timeout(idle_timeout_seconds: int | None) -> int:
    if idle_timeout_seconds is None:
        return SHELL_SESSION_IDLE_TIMEOUT_SECONDS
    return idle_timeout_seconds

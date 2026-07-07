from __future__ import annotations

import threading
import uuid
from pathlib import Path

from fastapi import HTTPException

from ..config import MAX_BASH_SESSIONS
from .models import BashCommand, BashSession, utcnow
from .process import kill_process
from .runtime_config import workspace


class BashSessionRegistryMixin:
    _sessions: dict[str, BashSession]
    _lock: threading.Lock
    max_sessions: int

    def create_session(
        self,
        *,
        session_id: str | None = None,
        exec_dir: Path | None = None,
        snapshot_path: str | None = None,
    ) -> BashSession:
        session_id = session_id or f"s_{uuid.uuid4().hex}"
        now = utcnow()
        session = BashSession(
            session_id=session_id,
            working_dir=exec_dir or workspace(),
            created_at=now,
            last_used_at=now,
            snapshot_path=snapshot_path,
        )

        with self._lock:
            if session_id in self._sessions:
                raise HTTPException(status_code=409, detail=f"session already exists: {session_id}")
            if self.max_sessions > 0 and len(self._sessions) >= self.max_sessions:
                raise HTTPException(status_code=429, detail="bash session limit exceeded")
            self._sessions[session_id] = session

        return session

    def close_session(self, session_id: str) -> BashSession:
        session = self.get(session_id)
        with session.lock:
            session.status = "closed"
            commands = list(session.commands.values())
            session.last_used_at = utcnow()

        for command in commands:
            if command.process.poll() is None:
                with command.lock:
                    command.killed = True
                    command.output_changed.notify_all()
                kill_process(command)

        return session

    def get(self, session_id: str) -> BashSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        return session

    def list(self) -> list[BashSession]:
        with self._lock:
            return list(self._sessions.values())

    def _get_or_create_session(self, *, session_id: str | None, exec_dir: Path | None) -> BashSession:
        if session_id:
            session = self.get(session_id)
            return session
        return self.create_session(exec_dir=exec_dir)

    def _select_command(self, session: BashSession, command_id: str | None) -> BashCommand:
        with session.lock:
            selected_id = command_id or session.current_command_id
            if not selected_id:
                raise HTTPException(status_code=404, detail=f"session has no commands: {session.session_id}")
            command = session.commands.get(selected_id)
        if not command:
            raise HTTPException(status_code=404, detail=f"command not found: {selected_id}")
        return command


def default_max_sessions(max_sessions: int | None) -> int:
    return MAX_BASH_SESSIONS if max_sessions is None else max_sessions

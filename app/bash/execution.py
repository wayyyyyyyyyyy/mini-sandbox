from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

from fastapi import HTTPException

from .models import BashCommand, BashSession, utcnow
from .output import read_stream
from .process import start_process, terminate
from .waiters import wait_for_command, wait_for_output, watch_timeout


class BashExecutionMixin:
    def _get_or_create_session(self, *, session_id: str | None, exec_dir: Path | None) -> BashSession:
        raise NotImplementedError

    def get(self, session_id: str) -> BashSession:
        raise NotImplementedError

    def _select_command(self, session: BashSession, command_id: str | None) -> BashCommand:
        raise NotImplementedError

    def exec(
        self,
        *,
        command: str,
        exec_dir: Path | None,
        env: dict[str, str],
        hard_timeout: float | None,
        session_id: str | None = None,
        async_mode: bool = False,
        timeout: float | None = None,
    ) -> tuple[BashSession, BashCommand]:
        session = self._get_or_create_session(session_id=session_id, exec_dir=exec_dir)
        with session.lock:
            if session.status == "closed":
                raise HTTPException(status_code=409, detail="session is closed")
            if exec_dir is not None:
                session.working_dir = exec_dir
            session.last_used_at = utcnow()
            working_dir = session.working_dir

        command_id = f"c_{uuid.uuid4().hex}"
        process = start_process(command, cwd=working_dir, env=env)
        bash_command = BashCommand(
            command_id=command_id,
            command=command,
            process=process,
            started_at=time.monotonic(),
            hard_timeout=hard_timeout,
        )

        with session.lock:
            session.commands[command_id] = bash_command
            session.current_command_id = command_id
            session.last_used_at = utcnow()

        threading.Thread(target=read_stream, args=(bash_command, "stdout"), daemon=True).start()
        threading.Thread(target=read_stream, args=(bash_command, "stderr"), daemon=True).start()
        if hard_timeout:
            threading.Thread(target=watch_timeout, args=(bash_command,), daemon=True).start()

        if not async_mode and timeout is not None:
            wait_for_command(bash_command, timeout)

        return session, bash_command

    def output(
        self,
        *,
        session_id: str,
        offset: int,
        stderr_offset: int,
        command_id: str | None = None,
        wait: bool = False,
        wait_timeout: float = 30,
    ) -> tuple[BashSession, BashCommand, str, str, int, int]:
        session = self.get(session_id)
        command = self._select_command(session, command_id)
        if wait:
            wait_for_output(command, offset, stderr_offset, wait_timeout)

        with command.lock:
            stdout = command.stdout[offset:]
            stderr = command.stderr[stderr_offset:]
            next_offset = len(command.stdout)
            next_stderr_offset = len(command.stderr)
        return session, command, stdout, stderr, next_offset, next_stderr_offset

    def kill(self, session_id: str, signal_name: str = "SIGTERM") -> tuple[BashSession, BashCommand]:
        session = self.get(session_id)
        command = self._select_command(session, None)
        terminate(command, signal_name)
        return session, command

    def write(self, *, session_id: str, input: str, command_id: str | None = None) -> tuple[BashSession, BashCommand]:
        session = self.get(session_id)
        command = self._select_command(session, command_id)
        if command.process.poll() is not None:
            raise HTTPException(status_code=409, detail="process is not running")
        if command.process.stdin is None:
            raise HTTPException(status_code=409, detail="stdin is not available")

        try:
            command.process.stdin.write(input)
            command.process.stdin.flush()
        except BrokenPipeError as exc:
            raise HTTPException(status_code=409, detail="stdin pipe is closed") from exc

        with session.lock:
            session.last_used_at = utcnow()

        return session, command

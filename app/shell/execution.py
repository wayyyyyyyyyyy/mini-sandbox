from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from fastapi import HTTPException

from .builtins import apply_stateful_shell_builtin
from .models import ShellSession, utcnow
from .output import read_process_output
from .process import interactive_shell_command, resize_pty, start_interactive_process, start_process


class ShellExecutionMixin:
    def create_session(self, *, session_id: str | None = None, exec_dir: Path | None = None) -> ShellSession:
        raise NotImplementedError

    def get(self, session_id: str) -> ShellSession:
        raise NotImplementedError

    def start_interactive(
        self,
        *,
        session_id: str | None,
        exec_dir: Path,
        cols: int | None = None,
        rows: int | None = None,
    ) -> ShellSession:
        session = self.create_session(session_id=session_id, exec_dir=exec_dir)
        with session.output_changed:
            if session.status == "closed":
                raise HTTPException(status_code=409, detail="shell session is closed")
            if session.current_process is not None and session.current_process.poll() is None:
                return session
            session.working_dir = exec_dir
            session.status = "ready"
            session.killed = False
            session.current_command = interactive_shell_command()
            session.exit_code = None
            session.last_used_at = utcnow()
            session.output_changed.notify_all()

        env = os.environ.copy()
        env.update(session.env)
        process, master_fd = start_interactive_process(session.working_dir, env, cols=cols, rows=rows)
        with session.output_changed:
            session.current_process = process
            session.current_master_fd = master_fd
            session.output_changed.notify_all()

        threading.Thread(target=read_process_output, args=(session, process, master_fd), daemon=True).start()
        return session

    def exec(
        self,
        *,
        command: str,
        session_id: str | None,
        exec_dir: Path | None,
        async_mode: bool,
        timeout: float | None,
        hard_timeout: float | None,
    ) -> ShellSession:
        session = self.create_session(session_id=session_id, exec_dir=exec_dir)
        with session.output_changed:
            if session.status == "closed":
                raise HTTPException(status_code=409, detail="shell session is closed")
            if session.current_process is not None and session.current_process.poll() is None:
                raise HTTPException(status_code=409, detail="shell process is already running")
            if exec_dir is not None:
                session.working_dir = exec_dir
            session.status = "ready"
            session.killed = False
            session.current_command = command
            session.exit_code = None
            session.last_used_at = utcnow()
            session.output_changed.notify_all()

        handled = apply_stateful_shell_builtin(session, command)
        if handled:
            return session

        env = os.environ.copy()
        env.update(session.env)
        process, master_fd = start_process(command, session.working_dir, env)
        with session.output_changed:
            session.current_process = process
            session.current_master_fd = master_fd
            session.output_changed.notify_all()

        threading.Thread(target=read_process_output, args=(session, process, master_fd), daemon=True).start()
        if hard_timeout:
            threading.Thread(target=self._watch_hard_timeout, args=(session, process, hard_timeout), daemon=True).start()

        if not async_mode:
            self._wait_for_process(session, process, timeout)
        return session

    def write(self, *, session_id: str, input: str, press_enter: bool) -> str:
        payload = input + ("\n" if press_enter else "")
        return self.write_raw(session_id=session_id, data=payload)

    def write_raw(self, *, session_id: str, data: str) -> str:
        session = self.get(session_id)
        with session.output_changed:
            process = session.current_process
            if process is None or process.poll() is not None:
                raise HTTPException(status_code=409, detail="shell process is not running")
            if session.current_master_fd is not None:
                try:
                    os.write(session.current_master_fd, data.encode("utf-8"))
                except OSError as exc:
                    raise HTTPException(status_code=409, detail="shell pty is closed") from exc
            else:
                stdin = process.stdin
                if stdin is None:
                    raise HTTPException(status_code=409, detail="shell stdin is not available")
                try:
                    stdin.write(data)
                    stdin.flush()
                except BrokenPipeError as exc:
                    raise HTTPException(status_code=409, detail="shell stdin is closed") from exc
            session.last_used_at = utcnow()
            return session.command_status

    def resize(self, *, session_id: str, cols: int, rows: int) -> None:
        session = self.get(session_id)
        with session.output_changed:
            master_fd = session.current_master_fd
        if master_fd is None or os.name == "nt":
            return
        resize_pty(master_fd, cols=cols, rows=rows)

    def wait(self, session_id: str, seconds: float | None) -> str:
        session = self.get(session_id)
        deadline = time.monotonic() + (seconds if seconds is not None else 0)
        with session.output_changed:
            while session.current_process is not None and session.current_process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return session.command_status
                session.output_changed.wait(timeout=remaining)
            return session.command_status

    def wait_for_output(self, session_id: str, offset: int, seconds: float) -> tuple[str, int, str]:
        session = self.get(session_id)
        deadline = time.monotonic() + seconds
        with session.output_changed:
            while len(session.output) <= offset and session.command_status == "running":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                session.output_changed.wait(timeout=remaining)
            output = session.output[offset:]
            return output, len(session.output), session.command_status

    def _wait_for_process(
        self,
        session: ShellSession,
        process: subprocess.Popen[str],
        timeout: float | None,
    ) -> None:
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return
        deadline = time.monotonic() + 1
        with session.output_changed:
            while session.exit_code is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                session.output_changed.wait(timeout=remaining)

    def kill(self, session_id: str) -> str:
        session = self.get(session_id)
        with session.output_changed:
            process = session.current_process
            if process is None or process.poll() is not None:
                return session.command_status
            session.killed = True
            session.status = "killed"
            session.output_changed.notify_all()
        if os.name == "nt":
            process.kill()
        else:
            process.send_signal(signal.SIGTERM)
        return "killed"

    def _watch_hard_timeout(self, session: ShellSession, process: subprocess.Popen[str], hard_timeout: float) -> None:
        time.sleep(hard_timeout)
        if process.poll() is None:
            with session.output_changed:
                session.killed = True
                session.status = "killed"
                session.output_changed.notify_all()
            process.kill()

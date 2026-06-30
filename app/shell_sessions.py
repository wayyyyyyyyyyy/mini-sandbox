from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from fastapi import HTTPException

from .config import MAX_SHELL_OUTPUT_CHARS, MAX_SHELL_SESSIONS, SHELL_SESSION_IDLE_TIMEOUT_SECONDS, WORKSPACE


@dataclass
class ShellSession:
    session_id: str
    working_dir: Path
    workspace: Path
    created_at: datetime
    last_used_at: datetime
    env: dict[str, str] = field(default_factory=dict)
    status: str = "ready"
    current_command: str | None = None
    current_process: subprocess.Popen[str] | None = None
    current_master_fd: int | None = None
    output: str = ""
    exit_code: int | None = None
    killed: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    output_changed: threading.Condition = field(init=False)

    def __post_init__(self) -> None:
        self.output_changed = threading.Condition(self.lock)

    @property
    def command_status(self) -> str:
        if self.status == "closed":
            return "closed"
        if self.killed:
            return "killed"
        if self.current_process is not None and self.current_process.poll() is None:
            return "running"
        if self.current_command is not None:
            return "completed"
        return self.status


class ShellSessionManager:
    def __init__(self, *, max_sessions: int | None = None, idle_timeout_seconds: int | None = None) -> None:
        self._sessions: dict[str, ShellSession] = {}
        self._lock = threading.Lock()
        self.max_sessions = MAX_SHELL_SESSIONS if max_sessions is None else max_sessions
        self.idle_timeout_seconds = (
            SHELL_SESSION_IDLE_TIMEOUT_SECONDS if idle_timeout_seconds is None else idle_timeout_seconds
        )

    def create_session(self, *, session_id: str | None = None, exec_dir: Path | None = None) -> ShellSession:
        session_id = session_id or f"sh_{uuid.uuid4().hex}"
        now = _utcnow()
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing
            if self.max_sessions > 0 and len(self._sessions) >= self.max_sessions:
                raise HTTPException(status_code=429, detail="shell session limit exceeded")
            session = ShellSession(
                session_id=session_id,
                working_dir=exec_dir or WORKSPACE,
                workspace=(exec_dir or WORKSPACE).resolve(),
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

    def cleanup_idle_sessions(self) -> list[str]:
        if self.idle_timeout_seconds <= 0:
            return []
        now = _utcnow()
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
            session.last_used_at = _utcnow()
            session.output_changed.notify_all()
        return session

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
            session.current_command = _interactive_shell_command()
            session.exit_code = None
            session.last_used_at = _utcnow()
            session.output_changed.notify_all()

        env = os.environ.copy()
        env.update(session.env)
        process, master_fd = self._start_interactive_process(session.working_dir, env, cols=cols, rows=rows)
        with session.output_changed:
            session.current_process = process
            session.current_master_fd = master_fd
            session.output_changed.notify_all()

        threading.Thread(target=self._read_process_output, args=(session, process, master_fd), daemon=True).start()
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
            session.last_used_at = _utcnow()
            session.output_changed.notify_all()

        handled = self._apply_stateful_shell_builtin(session, command)
        if handled:
            return session

        env = os.environ.copy()
        env.update(session.env)
        process, master_fd = self._start_process(command, session.working_dir, env)
        with session.output_changed:
            session.current_process = process
            session.current_master_fd = master_fd
            session.output_changed.notify_all()

        threading.Thread(target=self._read_process_output, args=(session, process, master_fd), daemon=True).start()
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
            session.last_used_at = _utcnow()
            return session.command_status

    def resize(self, *, session_id: str, cols: int, rows: int) -> None:
        session = self.get(session_id)
        with session.output_changed:
            master_fd = session.current_master_fd
        if master_fd is None or os.name == "nt":
            return
        _resize_pty(master_fd, cols=cols, rows=rows)

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

    def _start_process(
        self,
        command: str,
        cwd: Path,
        env: dict[str, str],
    ) -> tuple[subprocess.Popen[str], int | None]:
        if os.name == "nt":
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                shell=True,
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
            )
            return process, None

        import pty

        master_fd, slave_fd = pty.openpty()
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                shell=True,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                text=False,
            )
        finally:
            os.close(slave_fd)
        return process, master_fd

    def _start_interactive_process(
        self,
        cwd: Path,
        env: dict[str, str],
        *,
        cols: int | None,
        rows: int | None,
    ) -> tuple[subprocess.Popen[str], int | None]:
        if os.name == "nt":
            process = subprocess.Popen(
                _interactive_shell_command(),
                cwd=cwd,
                env=env,
                shell=False,
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
            )
            return process, None

        import pty

        master_fd, slave_fd = pty.openpty()
        if cols is not None and rows is not None:
            _resize_pty(master_fd, cols=cols, rows=rows)
        try:
            process = subprocess.Popen(
                [_interactive_shell_command()],
                cwd=cwd,
                env=env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                text=False,
            )
        finally:
            os.close(slave_fd)
        return process, master_fd

    def _apply_stateful_shell_builtin(self, session: ShellSession, command: str) -> bool:
        parts = [part.strip() for part in command.split("&&")]
        if not parts:
            return False
        handled_any = False
        for part in parts:
            if part.startswith("cd "):
                self._apply_cd(session, part[3:].strip())
                handled_any = True
                continue
            if part.startswith("export "):
                self._apply_export(session, part[7:].strip())
                handled_any = True
                continue
            return False
        if handled_any:
            with session.output_changed:
                session.exit_code = 0
                session.current_process = None
                session.output_changed.notify_all()
        return handled_any

    def _apply_cd(self, session: ShellSession, target: str) -> None:
        next_dir = (session.working_dir / target).resolve() if not Path(target).is_absolute() else Path(target).resolve()
        try:
            next_dir.relative_to(session.workspace)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=f"path escapes workspace: {target}") from exc
        if not next_dir.exists() or not next_dir.is_dir():
            raise HTTPException(status_code=400, detail=f"directory not found: {target}")
        with session.output_changed:
            session.working_dir = next_dir
            session.last_used_at = _utcnow()
            session.output_changed.notify_all()

    def _apply_export(self, session: ShellSession, assignment: str) -> None:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", assignment)
        if not match:
            raise HTTPException(status_code=400, detail=f"invalid export: {assignment}")
        key, value = match.groups()
        with session.output_changed:
            session.env[key] = value.strip("'\"")
            session.last_used_at = _utcnow()
            session.output_changed.notify_all()

    def _read_process_output(
        self,
        session: ShellSession,
        process: subprocess.Popen[str],
        master_fd: int | None,
    ) -> None:
        if master_fd is not None:
            self._read_pty_output(session, master_fd)
        elif process.stdout is not None:
            self._read_text_output(session, process.stdout)
        exit_code = process.wait()
        with session.output_changed:
            if session.current_process is process:
                session.exit_code = exit_code
                session.current_master_fd = None
                if session.status == "closed":
                    pass
                elif session.killed:
                    session.status = "killed"
                else:
                    session.status = "ready"
                session.output_changed.notify_all()

    def _read_text_output(self, session: ShellSession, stream: TextIO) -> None:
        for chunk in iter(stream.readline, ""):
            with session.output_changed:
                _append_output(session, chunk)
                session.output_changed.notify_all()

    def _read_pty_output(self, session: ShellSession, master_fd: int) -> None:
        try:
            while True:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                with session.output_changed:
                    _append_output(session, chunk.decode("utf-8", errors="replace"))
                    session.output_changed.notify_all()
        finally:
            try:
                os.close(master_fd)
            except OSError:
                pass

    def _watch_hard_timeout(self, session: ShellSession, process: subprocess.Popen[str], hard_timeout: float) -> None:
        time.sleep(hard_timeout)
        if process.poll() is None:
            with session.output_changed:
                session.killed = True
                session.status = "killed"
                session.output_changed.notify_all()
            process.kill()


def shell_result(session: ShellSession) -> dict:
    with session.output_changed:
        return {
            "session_id": session.session_id,
            "command": session.current_command,
            "status": session.command_status,
            "output": session.output,
            "exit_code": session.exit_code,
        }


def _append_output(session: ShellSession, chunk: str) -> None:
    session.output += chunk
    if MAX_SHELL_OUTPUT_CHARS > 0 and len(session.output) > MAX_SHELL_OUTPUT_CHARS:
        session.output = session.output[-MAX_SHELL_OUTPUT_CHARS:]


def _interactive_shell_command() -> str:
    if os.name == "nt":
        return os.environ.get("COMSPEC", "cmd.exe")
    return os.environ.get("SHELL", "/bin/bash")


def _resize_pty(master_fd: int, *, cols: int, rows: int) -> None:
    if os.name == "nt":
        return
    try:
        import fcntl
        import struct
        import termios

        size = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, size)
    except OSError:
        return


def shell_session_info(session: ShellSession) -> dict:
    now = _utcnow()
    with session.output_changed:
        return {
            "working_dir": str(session.working_dir),
            "created_at": session.created_at.isoformat(),
            "last_used_at": session.last_used_at.isoformat(),
            "age_seconds": int((now - session.created_at).total_seconds()),
            "status": session.command_status,
            "current_command": session.current_command,
        }


def _utcnow() -> datetime:
    return datetime.now(UTC)

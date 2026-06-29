import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException

from .config import MAX_COMMAND_OUTPUT_CHARS, WORKSPACE


@dataclass
class BashCommand:
    command_id: str
    command: str
    process: subprocess.Popen[str]
    started_at: float
    hard_timeout: float | None
    stdout: str = ""
    stderr: str = ""
    stdout_closed: bool = False
    stderr_closed: bool = False
    killed: bool = False
    timed_out: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    output_changed: threading.Condition = field(init=False)

    def __post_init__(self) -> None:
        self.output_changed = threading.Condition(self.lock)

    @property
    def status(self) -> str:
        if self.killed:
            return "killed"
        if self.timed_out:
            return "timed_out"
        if self.process.poll() is None:
            return "running"
        return "completed"

    @property
    def exit_code(self) -> int | None:
        return self.process.poll()


@dataclass
class BashSession:
    session_id: str
    working_dir: Path
    created_at: datetime
    last_used_at: datetime
    status: str = "ready"
    snapshot_path: str | None = None
    current_command_id: str | None = None
    commands: dict[str, BashCommand] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def command_count(self) -> int:
        return len(self.commands)

    @property
    def current_command(self) -> BashCommand | None:
        if not self.current_command_id:
            return None
        return self.commands.get(self.current_command_id)


class BashSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, BashSession] = {}
        self._lock = threading.Lock()

    def create_session(
        self,
        *,
        session_id: str | None = None,
        exec_dir: Path | None = None,
        snapshot_path: str | None = None,
    ) -> BashSession:
        session_id = session_id or f"s_{uuid.uuid4().hex}"
        now = _utcnow()
        session = BashSession(
            session_id=session_id,
            working_dir=exec_dir or WORKSPACE,
            created_at=now,
            last_used_at=now,
            snapshot_path=snapshot_path,
        )

        with self._lock:
            if session_id in self._sessions:
                raise HTTPException(status_code=409, detail=f"session already exists: {session_id}")
            self._sessions[session_id] = session

        return session

    def close_session(self, session_id: str) -> BashSession:
        session = self.get(session_id)
        with session.lock:
            session.status = "closed"
            commands = list(session.commands.values())
            session.last_used_at = _utcnow()

        for command in commands:
            if command.process.poll() is None:
                with command.lock:
                    command.killed = True
                    command.output_changed.notify_all()
                command.process.kill()

        return session

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
            session.last_used_at = _utcnow()
            working_dir = session.working_dir

        command_id = f"c_{uuid.uuid4().hex}"
        merged_env = os.environ.copy()
        merged_env.update(env)
        process = subprocess.Popen(
            command,
            cwd=working_dir,
            env=merged_env,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            bufsize=1,
        )
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
            session.last_used_at = _utcnow()

        threading.Thread(
            target=self._read_stream,
            args=(bash_command, "stdout"),
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_stream,
            args=(bash_command, "stderr"),
            daemon=True,
        ).start()
        if hard_timeout:
            threading.Thread(target=self._watch_timeout, args=(bash_command,), daemon=True).start()

        if not async_mode and timeout is not None:
            self._wait_for_command(bash_command, timeout)

        return session, bash_command

    def get(self, session_id: str) -> BashSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        return session

    def list(self) -> list[BashSession]:
        with self._lock:
            return list(self._sessions.values())

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
            self._wait_for_output(command, offset, stderr_offset, wait_timeout)

        with command.lock:
            stdout = command.stdout[offset:]
            stderr = command.stderr[stderr_offset:]
            next_offset = len(command.stdout)
            next_stderr_offset = len(command.stderr)
        return session, command, stdout, stderr, next_offset, next_stderr_offset

    def kill(self, session_id: str, signal_name: str = "SIGTERM") -> tuple[BashSession, BashCommand]:
        session = self.get(session_id)
        command = self._select_command(session, None)
        self._terminate(command, signal_name)
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
            session.last_used_at = _utcnow()

        return session, command

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

    def _read_stream(self, command: BashCommand, stream_name: str) -> None:
        stream = getattr(command.process, stream_name)
        if stream is None:
            return

        for chunk in iter(stream.readline, ""):
            with command.output_changed:
                existing = getattr(command, stream_name)
                setattr(command, stream_name, existing + chunk)
                command.output_changed.notify_all()

        with command.output_changed:
            setattr(command, f"{stream_name}_closed", True)
            command.output_changed.notify_all()

    def _watch_timeout(self, command: BashCommand) -> None:
        if not command.hard_timeout:
            return
        deadline = command.started_at + command.hard_timeout
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        if command.process.poll() is None:
            with command.output_changed:
                command.timed_out = True
                command.output_changed.notify_all()
            command.process.kill()

    def _wait_for_command(self, command: BashCommand, timeout: float) -> None:
        try:
            command.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return
        self._wait_for_streams(command, 1)
        with command.output_changed:
            command.output_changed.notify_all()

    def _wait_for_output(self, command: BashCommand, offset: int, stderr_offset: int, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        with command.output_changed:
            while command.process.poll() is None:
                if len(command.stdout) > offset or len(command.stderr) > stderr_offset:
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                command.output_changed.wait(timeout=remaining)

    def _wait_for_streams(self, command: BashCommand, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        with command.output_changed:
            while not command.stdout_closed or not command.stderr_closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                command.output_changed.wait(timeout=remaining)

    def _terminate(self, command: BashCommand, signal_name: str) -> None:
        if command.process.poll() is not None:
            return
        normalized = signal_name.upper()
        if normalized not in {"SIGTERM", "SIGKILL", "SIGINT"}:
            raise HTTPException(status_code=400, detail=f"unsupported signal: {signal_name}")
        with command.output_changed:
            command.killed = True
            command.output_changed.notify_all()
        if os.name == "nt":
            command.process.kill()
            return
        command.process.send_signal(getattr(signal, normalized))


def limit_output(value: str, max_chars: int | None = None) -> tuple[str, bool, int]:
    limit = MAX_COMMAND_OUTPUT_CHARS if max_chars is None else max_chars
    output_bytes = len(value.encode("utf-8"))
    if limit <= 0 or len(value) <= limit:
        return value, False, output_bytes

    marker = f"[output truncated: showing last {limit} characters of {output_bytes} bytes]\n"
    return marker + value[-limit:], True, output_bytes


def _utcnow() -> datetime:
    return datetime.now(UTC)

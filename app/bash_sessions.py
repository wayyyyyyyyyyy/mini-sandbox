import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException

from .config import MAX_COMMAND_OUTPUT_CHARS


@dataclass
class BashSession:
    session_id: str
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


class BashSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, BashSession] = {}
        self._lock = threading.Lock()

    def exec(
        self,
        *,
        command: str,
        exec_dir: Path,
        env: dict[str, str],
        hard_timeout: float | None,
    ) -> BashSession:
        session_id = f"s_{uuid.uuid4().hex}"
        command_id = f"c_{uuid.uuid4().hex}"
        merged_env = os.environ.copy()
        merged_env.update(env)

        process = subprocess.Popen(
            command,
            cwd=exec_dir,
            env=merged_env,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            bufsize=1,
        )
        session = BashSession(
            session_id=session_id,
            command_id=command_id,
            command=command,
            process=process,
            started_at=time.monotonic(),
            hard_timeout=hard_timeout,
        )

        with self._lock:
            self._sessions[session_id] = session

        threading.Thread(
            target=self._read_stream,
            args=(session, "stdout"),
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_stream,
            args=(session, "stderr"),
            daemon=True,
        ).start()
        if hard_timeout:
            threading.Thread(target=self._watch_timeout, args=(session,), daemon=True).start()

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

    def output(
        self,
        *,
        session_id: str,
        offset: int,
        stderr_offset: int,
    ) -> tuple[BashSession, str, str, int, int]:
        session = self.get(session_id)
        with session.lock:
            stdout = session.stdout[offset:]
            stderr = session.stderr[stderr_offset:]
            next_offset = len(session.stdout)
            next_stderr_offset = len(session.stderr)
        return session, stdout, stderr, next_offset, next_stderr_offset

    def kill(self, session_id: str) -> BashSession:
        session = self.get(session_id)
        with session.lock:
            session.killed = True
        if session.process.poll() is None:
            session.process.kill()
        return session

    def write(self, *, session_id: str, input: str) -> BashSession:
        session = self.get(session_id)
        if session.process.poll() is not None:
            raise HTTPException(status_code=409, detail="process is not running")
        if session.process.stdin is None:
            raise HTTPException(status_code=409, detail="stdin is not available")

        try:
            session.process.stdin.write(input)
            session.process.stdin.flush()
        except BrokenPipeError as exc:
            raise HTTPException(status_code=409, detail="stdin pipe is closed") from exc

        return session

    def _read_stream(self, session: BashSession, stream_name: str) -> None:
        stream = getattr(session.process, stream_name)
        if stream is None:
            return

        for chunk in iter(stream.readline, ""):
            with session.lock:
                existing = getattr(session, stream_name)
                setattr(session, stream_name, existing + chunk)

        with session.lock:
            setattr(session, f"{stream_name}_closed", True)

    def _watch_timeout(self, session: BashSession) -> None:
        if not session.hard_timeout:
            return
        deadline = session.started_at + session.hard_timeout
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        if session.process.poll() is None:
            with session.lock:
                session.timed_out = True
            session.process.kill()


def limit_output(value: str) -> tuple[str, bool, int]:
    output_bytes = len(value.encode("utf-8"))
    if MAX_COMMAND_OUTPUT_CHARS <= 0 or len(value) <= MAX_COMMAND_OUTPUT_CHARS:
        return value, False, output_bytes

    marker = (
        f"[output truncated: showing last {MAX_COMMAND_OUTPUT_CHARS} characters "
        f"of {output_bytes} bytes]\n"
    )
    return marker + value[-MAX_COMMAND_OUTPUT_CHARS:], True, output_bytes

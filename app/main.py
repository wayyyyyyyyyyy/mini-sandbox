import os
import platform
import subprocess
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException

from .auth import require_api_key
from .bash_sessions import BashSessionManager, limit_output
from .config import DEFAULT_COMMAND_TIMEOUT, MAX_COMMAND_TIMEOUT, WORKSPACE
from .schemas import (
    BashCommandResult,
    BashOutputResult,
    BashExecRequest,
    BashKillRequest,
    BashOutputRequest,
    BashSessionInfo,
    BashSessionCreateRequest,
    BashSessionListResult,
    BashWriteRequest,
    FileInfo,
    FileListRequest,
    FileListResult,
    FileReadRequest,
    FileReadResult,
    FileWriteRequest,
    FileWriteResult,
    SandboxContext,
    ShellExecRequest,
    ShellExecResult,
)
from .security import ensure_file_size_allowed, ensure_workspace, resolve_workspace_path

bash_sessions = BashSessionManager()

app = FastAPI(
    title="Mini Agent Sandbox",
    description="A minimal Docker-backed sandbox API for learning agent infrastructure.",
    version="0.1.0",
)


@app.on_event("startup")
def startup() -> None:
    ensure_workspace()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/context", response_model=SandboxContext)
def get_context(_: None = Depends(require_api_key)) -> SandboxContext:
    return SandboxContext(
        workspace=str(WORKSPACE),
        user=os.getenv("USER") or os.getenv("USERNAME") or "unknown",
        cwd=os.getcwd(),
        python_version=platform.python_version(),
    )


@app.post("/shell/exec", response_model=ShellExecResult)
def shell_exec(request: ShellExecRequest, _: None = Depends(require_api_key)) -> ShellExecResult:
    timeout = request.timeout or DEFAULT_COMMAND_TIMEOUT
    timeout = min(timeout, MAX_COMMAND_TIMEOUT)
    exec_dir = resolve_workspace_path(request.exec_dir or ".")
    if not exec_dir.exists() or not exec_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"exec_dir is not a directory: {exec_dir}")

    env = os.environ.copy()
    env.update(request.env)
    start = time.monotonic()

    try:
        completed = subprocess.run(
            request.command,
            cwd=exec_dir,
            env=env,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        status = "completed"
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        status = "timed_out"
        exit_code = None
        stdout = _to_text(exc.stdout)
        stderr = _to_text(exc.stderr)

    stdout_text, stdout_truncated, stdout_bytes = limit_output(stdout)
    stderr_text, stderr_truncated, stderr_bytes = limit_output(stderr)

    return ShellExecResult(
        command=request.command,
        status=status,
        stdout=stdout_text,
        stderr=stderr_text,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        exit_code=exit_code,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


@app.post("/bash/exec", response_model=BashCommandResult)
def bash_exec(request: BashExecRequest, _: None = Depends(require_api_key)) -> BashCommandResult:
    if request.exec_dir is not None:
        exec_dir = _resolve_exec_dir(request.exec_dir)
    elif request.session_id is None:
        exec_dir = _resolve_exec_dir(".")
    else:
        exec_dir = None

    session, command = bash_sessions.exec(
        session_id=request.session_id,
        command=request.command,
        exec_dir=exec_dir,
        env=request.env,
        hard_timeout=request.hard_timeout,
        async_mode=request.async_mode,
        timeout=request.timeout,
    )
    if not request.async_mode and request.timeout is not None and command.status != "running":
        with command.lock:
            stdout = command.stdout
            stderr = command.stderr
            stdout_offset = len(command.stdout)
            stderr_offset = len(command.stderr)
    else:
        stdout = ""
        stderr = ""
        stdout_offset = len(command.stdout)
        stderr_offset = len(command.stderr)

    return _bash_result(command, stdout, stderr, stdout_offset, stderr_offset, request.max_output_length)


@app.post("/bash/output", response_model=BashOutputResult)
def bash_output(request: BashOutputRequest, _: None = Depends(require_api_key)) -> BashOutputResult:
    _, command, stdout, stderr, offset, stderr_offset = bash_sessions.output(
        session_id=request.session_id,
        command_id=request.command_id,
        offset=request.offset,
        stderr_offset=request.stderr_offset,
        wait=request.wait,
        wait_timeout=request.wait_timeout,
    )
    return _bash_output_result(command, stdout, stderr, offset, stderr_offset)


@app.post("/bash/kill", response_model=BashCommandResult)
def bash_kill(request: BashKillRequest, _: None = Depends(require_api_key)) -> BashCommandResult:
    _, command = bash_sessions.kill(request.session_id, request.signal)
    _, command, stdout, stderr, offset, stderr_offset = bash_sessions.output(
        session_id=request.session_id,
        command_id=command.command_id,
        offset=0,
        stderr_offset=0,
    )
    return _bash_result(command, stdout, stderr, offset, stderr_offset)


@app.post("/bash/write", response_model=BashCommandResult)
def bash_write(request: BashWriteRequest, _: None = Depends(require_api_key)) -> BashCommandResult:
    session, command = bash_sessions.write(
        session_id=request.session_id,
        command_id=request.command_id,
        input=request.input,
    )
    _, command, stdout, stderr, offset, stderr_offset = bash_sessions.output(
        session_id=session.session_id,
        command_id=command.command_id,
        offset=0,
        stderr_offset=0,
    )
    return _bash_result(command, stdout, stderr, offset, stderr_offset)


@app.get("/bash/sessions", response_model=BashSessionListResult)
def bash_list_sessions(_: None = Depends(require_api_key)) -> BashSessionListResult:
    sessions = []
    for session in bash_sessions.list():
        sessions.append(_bash_session_info(session))
    return BashSessionListResult(sessions=sessions)


@app.post("/bash/sessions/create", response_model=BashSessionInfo)
def bash_create_session(request: BashSessionCreateRequest, _: None = Depends(require_api_key)) -> BashSessionInfo:
    exec_dir = _resolve_exec_dir(request.exec_dir or ".")
    session = bash_sessions.create_session(
        session_id=request.session_id,
        exec_dir=exec_dir,
        snapshot_path=request.snapshot_path,
    )
    return _bash_session_info(session)


@app.post("/bash/sessions/{session_id}/close", response_model=dict[str, bool])
def bash_close_session(session_id: str, _: None = Depends(require_api_key)) -> dict[str, bool]:
    bash_sessions.close_session(session_id)
    return {"success": True}


@app.post("/file/read", response_model=FileReadResult)
def file_read(request: FileReadRequest, _: None = Depends(require_api_key)) -> FileReadResult:
    path = resolve_workspace_path(request.path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {request.path}")

    content_bytes = path.read_bytes()
    ensure_file_size_allowed(content_bytes)
    return FileReadResult(
        path=_relative(path),
        content=content_bytes.decode("utf-8"),
        bytes=len(content_bytes),
    )


@app.post("/file/write", response_model=FileWriteResult)
def file_write(request: FileWriteRequest, _: None = Depends(require_api_key)) -> FileWriteResult:
    path = resolve_workspace_path(request.path)
    content_bytes = request.content.encode("utf-8")
    ensure_file_size_allowed(content_bytes)

    if request.create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.parent.exists():
        raise HTTPException(status_code=400, detail=f"parent does not exist: {path.parent}")

    path.write_bytes(content_bytes)
    return FileWriteResult(path=_relative(path), bytes=len(content_bytes))


@app.post("/file/list", response_model=FileListResult)
def file_list(request: FileListRequest, _: None = Depends(require_api_key)) -> FileListResult:
    path = resolve_workspace_path(request.path)
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=404, detail=f"directory not found: {request.path}")

    entries = []
    for child in sorted(path.iterdir(), key=lambda item: item.name):
        if child.is_file():
            kind = "file"
        elif child.is_dir():
            kind = "directory"
        else:
            kind = "other"
        entries.append(FileInfo(path=_relative(child), kind=kind, bytes=_size(child)))

    return FileListResult(path=_relative(path), entries=entries)


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(WORKSPACE))


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return 0


def _to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _resolve_exec_dir(path: str) -> Path:
    exec_dir = resolve_workspace_path(path)
    if not exec_dir.exists() or not exec_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"exec_dir is not a directory: {exec_dir}")
    return exec_dir


def _bash_session_info(session) -> BashSessionInfo:
    command = session.current_command
    stdout_offset = None
    stderr_offset = None
    duration_ms = None
    exit_code = None
    command_id = None
    command_text = None
    current_command = None
    if command is not None:
        with command.lock:
            stdout_offset = len(command.stdout)
            stderr_offset = len(command.stderr)
        duration_ms = int((time.monotonic() - command.started_at) * 1000)
        exit_code = command.exit_code
        command_id = command.command_id
        command_text = command.command
        current_command = command.command

    return BashSessionInfo(
        session_id=session.session_id,
        status=session.status,
        working_dir=str(session.working_dir),
        created_at=session.created_at.isoformat(),
        last_used_at=session.last_used_at.isoformat(),
        current_command=current_command,
        command_count=session.command_count,
        command_id=command_id,
        command=command_text,
        stdout_offset=stdout_offset,
        stderr_offset=stderr_offset,
        exit_code=exit_code,
        duration_ms=duration_ms,
    )


def _bash_result(
    command,
    stdout: str,
    stderr: str,
    stdout_offset: int,
    stderr_offset: int,
    max_output_length: int | None = None,
) -> BashCommandResult:
    stdout_text, stdout_truncated, stdout_bytes = limit_output(stdout, max_output_length)
    stderr_text, stderr_truncated, stderr_bytes = limit_output(stderr, max_output_length)
    return BashCommandResult(
        session_id=_command_session_id(command.command_id),
        command_id=command.command_id,
        command=command.command,
        status=command.status,
        stdout=stdout_text,
        stderr=stderr_text,
        stdout_offset=stdout_offset,
        stderr_offset=stderr_offset,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        exit_code=command.exit_code,
    )


def _bash_output_result(
    command,
    stdout: str,
    stderr: str,
    stdout_offset: int,
    stderr_offset: int,
) -> BashOutputResult:
    result = _bash_result(command, stdout, stderr, stdout_offset, stderr_offset)
    return BashOutputResult(
        **result.model_dump(),
        offset=stdout_offset,
        command_info={
            "command_id": command.command_id,
            "command": command.command,
            "status": command.status,
            "exit_code": command.exit_code,
        },
    )


def _command_session_id(command_id: str) -> str:
    for session in bash_sessions.list():
        if command_id in session.commands:
            return session.session_id
    raise HTTPException(status_code=404, detail=f"command session not found: {command_id}")

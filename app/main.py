import os
import platform
import subprocess
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException

from .auth import require_api_key
from .config import DEFAULT_COMMAND_TIMEOUT, MAX_COMMAND_OUTPUT_CHARS, MAX_COMMAND_TIMEOUT, WORKSPACE
from .schemas import (
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

    stdout_text, stdout_truncated, stdout_bytes = _limit_output(stdout)
    stderr_text, stderr_truncated, stderr_bytes = _limit_output(stderr)

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


def _limit_output(value: str) -> tuple[str, bool, int]:
    output_bytes = len(value.encode("utf-8"))
    if MAX_COMMAND_OUTPUT_CHARS <= 0 or len(value) <= MAX_COMMAND_OUTPUT_CHARS:
        return value, False, output_bytes

    marker = (
        f"[output truncated: showing last {MAX_COMMAND_OUTPUT_CHARS} characters "
        f"of {output_bytes} bytes]\n"
    )
    return marker + value[-MAX_COMMAND_OUTPUT_CHARS:], True, output_bytes

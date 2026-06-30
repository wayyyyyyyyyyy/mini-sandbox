import base64
import fnmatch
import json
import os
import platform
import re
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

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
    FileFindRequest,
    FileFindResult,
    FileGlobRequest,
    FileGlobResult,
    FileGrepRequest,
    FileGrepResult,
    FileReplaceRequest,
    FileReplaceResult,
    FileSearchRequest,
    FileSearchResult,
    FileListRequest,
    FileListResult,
    FileReadRequest,
    FileReadResult,
    SandboxResponse,
    FileWriteRequest,
    FileWriteResult,
    SandboxContext,
    ShellExecRequest,
    ShellExecResult,
)
from .security import ensure_file_size_allowed, ensure_workspace, resolve_workspace_path

bash_sessions = BashSessionManager()


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_workspace()
    yield


app = FastAPI(
    title="Mini Agent Sandbox",
    description="A minimal Docker-backed sandbox API for learning agent infrastructure.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def wrap_json_api_response(request: Request, call_next):
    response = await call_next(request)
    if _skip_response_wrapper(request.url.path) or response.headers.get("x-sandbox-wrapped") == "true":
        return response

    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        return response

    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    try:
        data = json.loads(body.decode("utf-8")) if body else None
    except json.JSONDecodeError:
        return JSONResponse(
            status_code=response.status_code,
            content=_response_payload(False, "Invalid JSON response", None),
        )

    success = response.status_code < 400
    message = "Operation successful" if success else _error_message_from_data(data)
    wrapped = _response_payload(success, message, data if success else None)
    return JSONResponse(
        status_code=response.status_code,
        content=wrapped,
        headers=_forward_headers(response.headers),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "HTTP error"
    data = None if isinstance(exc.detail, str) else exc.detail
    return JSONResponse(
        status_code=exc.status_code,
        content=_response_payload(False, message, data),
        headers={"x-sandbox-wrapped": "true"},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=_response_payload(False, "Validation error", exc.errors()),
        headers={"x-sandbox-wrapped": "true"},
    )


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
    content = content_bytes.decode("utf-8")
    line_count = None
    if request.start_line is not None or request.end_line is not None:
        content = content.replace("\r\n", "\n")
        lines = content.splitlines(keepends=True)
        start = request.start_line or 0
        end = request.end_line if request.end_line is not None else len(lines)
        if end < start:
            raise HTTPException(status_code=400, detail="end_line must be greater than or equal to start_line")
        selected = lines[start:end]
        content = "".join(selected)
        line_count = len(selected)

    return FileReadResult(
        path=_relative(path),
        content=content,
        bytes=len(content.encode("utf-8")),
        line_count=line_count,
    )


@app.post("/file/write", response_model=FileWriteResult)
def file_write(request: FileWriteRequest, _: None = Depends(require_api_key)) -> FileWriteResult:
    path = resolve_workspace_path(request.path)
    content_bytes = _file_content_bytes(request)
    ensure_file_size_allowed(content_bytes)

    if request.create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.parent.exists():
        raise HTTPException(status_code=400, detail=f"parent does not exist: {path.parent}")

    if request.append:
        with path.open("ab") as file:
            file.write(content_bytes)
    else:
        path.write_bytes(content_bytes)
    return FileWriteResult(path=_relative(path), bytes=path.stat().st_size)


@app.post("/file/replace", response_model=FileReplaceResult)
def file_replace(request: FileReplaceRequest, _: None = Depends(require_api_key)) -> FileReplaceResult:
    path = resolve_workspace_path(request.path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {request.path}")

    content = path.read_text(encoding="utf-8")
    if request.old_str not in content:
        raise HTTPException(status_code=404, detail=f"old_str not found in file: {request.path}")

    match_count = content.count(request.old_str)
    if request.count is not None:
        replacement_limit = request.count
    elif request.all:
        replacement_limit = match_count
    else:
        replacement_limit = 1

    replaced = min(match_count, replacement_limit)
    updated = content.replace(request.old_str, request.new_str, replacement_limit)
    ensure_file_size_allowed(updated.encode("utf-8"))
    path.write_bytes(updated.encode("utf-8"))

    return FileReplaceResult(path=_relative(path), replaced=replaced, changed=replaced > 0)


@app.post("/file/upload", response_model=FileWriteResult)
async def file_upload(
    path: str = Form(...),
    file: UploadFile = File(...),
    _: None = Depends(require_api_key),
) -> FileWriteResult:
    target = resolve_workspace_path(path)
    content = await file.read()
    ensure_file_size_allowed(content)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return FileWriteResult(path=_relative(target), bytes=target.stat().st_size)


@app.get("/file/download")
def file_download(path: str, _: None = Depends(require_api_key)) -> FileResponse:
    target = resolve_workspace_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {path}")
    return FileResponse(path=target, filename=target.name, media_type="application/octet-stream")


@app.post("/file/list", response_model=FileListResult)
def file_list(request: FileListRequest, _: None = Depends(require_api_key)) -> FileListResult:
    path = resolve_workspace_path(request.path)
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=404, detail=f"directory not found: {request.path}")

    entries = []
    children = path.rglob("*") if request.recursive else path.iterdir()
    for child in sorted(children, key=lambda item: str(item.relative_to(path))):
        if not request.show_hidden and _is_hidden_relative(child.relative_to(path)):
            continue
        if child.is_file():
            kind = "file"
        elif child.is_dir():
            kind = "directory"
        else:
            kind = "other"
        entries.append(
            FileInfo(
                path=_relative(child),
                kind=kind,
                bytes=_size(child) if request.include_size else 0,
            )
        )

    return FileListResult(path=_relative(path), entries=entries)


@app.post("/file/find", response_model=FileFindResult)
def file_find(request: FileFindRequest, _: None = Depends(require_api_key)) -> FileFindResult:
    path = resolve_workspace_path(request.path)
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=404, detail=f"directory not found: {request.path}")

    files = []
    for child in sorted(path.rglob(request.glob), key=lambda item: _relative(item)):
        if len(files) >= request.max_results:
            break
        if not child.is_file():
            continue
        relative_to_root = child.relative_to(path)
        if not request.include_hidden and _is_hidden_relative(relative_to_root):
            continue
        files.append(_relative(child))

    return FileFindResult(path=_relative(path), glob=request.glob, files=files)


@app.post("/file/glob", response_model=FileGlobResult)
def file_glob(request: FileGlobRequest, _: None = Depends(require_api_key)) -> FileGlobResult:
    path = resolve_workspace_path(request.path)
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=404, detail=f"directory not found: {request.path}")

    candidates = list(path.glob(request.pattern))
    if request.sort_by == "name":
        candidates.sort(key=lambda item: item.name)
    else:
        candidates.sort(key=lambda item: _relative(item))

    matches = []
    entries = []
    for child in candidates:
        if len(matches) >= request.max_results:
            break
        if request.files_only and not child.is_file():
            continue
        relative_to_root = child.relative_to(path)
        relative_text = relative_to_root.as_posix()
        if not request.include_hidden and _is_hidden_relative(relative_to_root):
            continue
        if _matches_any(relative_text, request.exclude):
            continue
        matches.append(_relative(child))
        if request.include_metadata:
            if child.is_file():
                kind = "file"
            elif child.is_dir():
                kind = "directory"
            else:
                kind = "other"
            entries.append(FileInfo(path=_relative(child), kind=kind, bytes=_size(child)))

    return FileGlobResult(path=_relative(path), pattern=request.pattern, matches=matches, entries=entries)


@app.post("/file/search", response_model=FileSearchResult)
def file_search(request: FileSearchRequest, _: None = Depends(require_api_key)) -> FileSearchResult:
    path = resolve_workspace_path(request.path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {request.path}")

    flags = re.IGNORECASE if request.case_insensitive else 0
    try:
        pattern = re.compile(request.regex, flags)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"invalid regex: {exc}") from exc

    content = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    matches = []
    for line_number, line in enumerate(content.splitlines()):
        for match in pattern.finditer(line):
            matches.append(
                {
                    "line": line_number,
                    "text": line,
                    "match": match.group(0),
                }
            )
            if len(matches) >= request.max_results:
                return FileSearchResult(path=_relative(path), regex=request.regex, matches=matches)

    return FileSearchResult(path=_relative(path), regex=request.regex, matches=matches)


@app.post("/file/grep", response_model=FileGrepResult)
def file_grep(request: FileGrepRequest, _: None = Depends(require_api_key)) -> FileGrepResult:
    path = resolve_workspace_path(request.path)
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=404, detail=f"directory not found: {request.path}")

    flags = re.IGNORECASE if request.case_insensitive else 0
    try:
        pattern = re.compile(request.pattern, flags)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"invalid regex: {exc}") from exc

    matches = []
    for child in sorted(path.rglob("*"), key=lambda item: _relative(item)):
        if len(matches) >= request.max_results:
            break
        if not child.is_file():
            continue
        relative_text = child.relative_to(path).as_posix()
        if request.include and not _matches_any(relative_text, request.include):
            continue
        if _matches_any(relative_text, request.exclude):
            continue
        try:
            content = child.read_text(encoding="utf-8").replace("\r\n", "\n")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(content.splitlines()):
            for match in pattern.finditer(line):
                matches.append(
                    {
                        "path": _relative(child),
                        "line": line_number,
                        "text": line,
                        "match": match.group(0),
                    }
                )
                if len(matches) >= request.max_results:
                    return FileGrepResult(path=_relative(path), pattern=request.pattern, matches=matches)

    return FileGrepResult(path=_relative(path), pattern=request.pattern, matches=matches)


def _relative(path: Path) -> str:
    return path.resolve().relative_to(WORKSPACE).as_posix()


def _response_payload(success: bool, message: str, data, hint: str | None = None) -> dict:
    return SandboxResponse(success=success, message=message, data=data, hint=hint).model_dump()


def _forward_headers(headers) -> dict[str, str]:
    excluded = {"content-length", "content-type"}
    return {key: value for key, value in headers.items() if key.lower() not in excluded}


def _skip_response_wrapper(path: str) -> bool:
    return path in {"/healthz", "/openapi.json"} or path.startswith(("/docs", "/redoc"))


def _error_message_from_data(data) -> str:
    if isinstance(data, dict) and isinstance(data.get("detail"), str):
        return data["detail"]
    return "HTTP error"


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return 0


def _file_content_bytes(request: FileWriteRequest) -> bytes:
    content = request.content
    if request.leading_newline:
        content = "\n" + content
    if request.trailing_newline:
        content = content + "\n"

    if request.encoding == "utf-8":
        return content.encode("utf-8")
    if request.encoding == "base64":
        try:
            return base64.b64decode(content, validate=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid base64 content") from exc
    if request.encoding == "raw":
        return content.encode("latin-1")
    raise HTTPException(status_code=400, detail=f"unsupported encoding: {request.encoding}")


def _is_hidden_relative(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


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

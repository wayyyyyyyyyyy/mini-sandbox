import time
from collections.abc import Callable
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException

from ..auth import require_api_key
from ..bash_sessions import BashSessionManager, limit_output
from ..schemas import (
    BashCommandResult,
    BashExecRequest,
    BashKillRequest,
    BashOutputRequest,
    BashOutputResult,
    BashSessionCreateRequest,
    BashSessionInfo,
    BashSessionListResult,
    BashWriteRequest,
)


def register_bash_routes(
    app: FastAPI,
    bash_sessions: BashSessionManager,
    resolve_exec_dir: Callable[[str], Path],
) -> None:
    @app.post("/bash/exec", response_model=BashCommandResult)
    def bash_exec(request: BashExecRequest, _: None = Depends(require_api_key)) -> BashCommandResult:
        if request.exec_dir is not None:
            exec_dir = resolve_exec_dir(request.exec_dir)
        elif request.session_id is None:
            exec_dir = resolve_exec_dir(".")
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

        return _bash_result(bash_sessions, command, stdout, stderr, stdout_offset, stderr_offset, request.max_output_length)

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
        return _bash_output_result(bash_sessions, command, stdout, stderr, offset, stderr_offset)

    @app.post("/bash/kill", response_model=BashCommandResult)
    def bash_kill(request: BashKillRequest, _: None = Depends(require_api_key)) -> BashCommandResult:
        _, command = bash_sessions.kill(request.session_id, request.signal)
        _, command, stdout, stderr, offset, stderr_offset = bash_sessions.output(
            session_id=request.session_id,
            command_id=command.command_id,
            offset=0,
            stderr_offset=0,
        )
        return _bash_result(bash_sessions, command, stdout, stderr, offset, stderr_offset)

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
        return _bash_result(bash_sessions, command, stdout, stderr, offset, stderr_offset)

    @app.get("/bash/sessions", response_model=BashSessionListResult)
    def bash_list_sessions(_: None = Depends(require_api_key)) -> BashSessionListResult:
        sessions = []
        for session in bash_sessions.list():
            sessions.append(_bash_session_info(session))
        return BashSessionListResult(sessions=sessions)

    @app.post("/bash/sessions/create", response_model=BashSessionInfo)
    def bash_create_session(request: BashSessionCreateRequest, _: None = Depends(require_api_key)) -> BashSessionInfo:
        exec_dir = resolve_exec_dir(request.exec_dir or ".")
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
    bash_sessions: BashSessionManager,
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
        session_id=_command_session_id(bash_sessions, command.command_id),
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
    bash_sessions: BashSessionManager,
    command,
    stdout: str,
    stderr: str,
    stdout_offset: int,
    stderr_offset: int,
) -> BashOutputResult:
    result = _bash_result(bash_sessions, command, stdout, stderr, stdout_offset, stderr_offset)
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


def _command_session_id(bash_sessions: BashSessionManager, command_id: str) -> str:
    for session in bash_sessions.list():
        if command_id in session.commands:
            return session.session_id
    raise HTTPException(status_code=404, detail=f"command session not found: {command_id}")

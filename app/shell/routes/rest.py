from collections.abc import Callable
from pathlib import Path

from fastapi import Depends, FastAPI

from ...auth import require_api_key
from ...schemas import (
    ShellExecRequest,
    ShellExecResult,
    ShellKillRequest,
    ShellKillResult,
    ShellViewRequest,
    ShellViewResult,
    ShellWaitRequest,
    ShellWaitResult,
    ShellWriteRequest,
    ShellWriteResult,
)
from ...shell_sessions import ShellSessionManager, shell_result


def register_shell_rest_routes(
    app: FastAPI,
    shell_sessions: ShellSessionManager,
    resolve_exec_dir: Callable[[str], Path],
) -> None:
    @app.post("/shell/exec", response_model=ShellExecResult)
    def shell_exec(request: ShellExecRequest, _: None = Depends(require_api_key)) -> ShellExecResult:
        if request.exec_dir is not None:
            exec_dir = resolve_exec_dir(request.exec_dir)
        elif request.id is None:
            exec_dir = resolve_exec_dir(".")
        else:
            exec_dir = None
        session = shell_sessions.exec(
            command=request.command,
            session_id=request.id,
            exec_dir=exec_dir,
            async_mode=request.async_mode,
            timeout=request.timeout,
            hard_timeout=request.hard_timeout,
        )
        return ShellExecResult(**shell_result(session))

    @app.post("/shell/view", response_model=ShellViewResult)
    def shell_view(request: ShellViewRequest, _: None = Depends(require_api_key)) -> ShellViewResult:
        session = shell_sessions.get(request.id)
        return ShellViewResult(**shell_result(session))

    @app.post("/shell/wait", response_model=ShellWaitResult)
    def shell_wait(request: ShellWaitRequest, _: None = Depends(require_api_key)) -> ShellWaitResult:
        return ShellWaitResult(status=shell_sessions.wait(request.id, request.seconds))

    @app.post("/shell/write", response_model=ShellWriteResult)
    def shell_write(request: ShellWriteRequest, _: None = Depends(require_api_key)) -> ShellWriteResult:
        return ShellWriteResult(
            status=shell_sessions.write(
                session_id=request.id,
                input=request.input,
                press_enter=request.press_enter,
            )
        )

    @app.post("/shell/kill", response_model=ShellKillResult)
    def shell_kill(request: ShellKillRequest, _: None = Depends(require_api_key)) -> ShellKillResult:
        session = shell_sessions.get(request.id)
        status = shell_sessions.kill(request.id)
        return ShellKillResult(status=status, exit_code=session.exit_code, returncode=session.exit_code)

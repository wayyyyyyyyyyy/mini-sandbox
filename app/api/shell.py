import asyncio
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect

from ..auth import create_ticket, require_api_key, require_http_credentials
from ..schemas import (
    ShellCreateSessionRequest,
    ShellCreateSessionResponse,
    ShellExecRequest,
    ShellExecResult,
    ShellKillRequest,
    ShellKillResult,
    ShellSessionListResult,
    ShellSessionStats,
    ShellTerminalUrlResult,
    ShellUpdateSessionRequest,
    ShellUpdateSessionResult,
    ShellViewRequest,
    ShellViewResult,
    ShellWaitRequest,
    ShellWaitResult,
    ShellWriteRequest,
    ShellWriteResult,
)
from ..shell_sessions import ShellSessionManager, shell_result, shell_session_info


def register_shell_routes(
    app: FastAPI,
    shell_sessions: ShellSessionManager,
    resolve_exec_dir: Callable[[str], Path],
) -> None:
    @app.websocket("/shell/ws")
    async def shell_websocket(websocket: WebSocket):
        try:
            require_http_credentials(
                x_sandbox_api_key=websocket.headers.get("x-sandbox-api-key"),
                authorization=websocket.headers.get("authorization"),
                ticket=websocket.query_params.get("ticket"),
            )
        except HTTPException:
            await websocket.close(code=1008)
            return

        exec_dir = resolve_exec_dir(websocket.query_params.get("exec_dir") or ".")
        session = shell_sessions.start_interactive(
            session_id=websocket.query_params.get("session_id"),
            exec_dir=exec_dir,
            cols=_optional_int(websocket.query_params.get("cols")),
            rows=_optional_int(websocket.query_params.get("rows")),
        )
        await websocket.accept()
        await websocket.send_json({"type": "session", "session_id": session.session_id})

        stop_event = asyncio.Event()
        sender = asyncio.create_task(
            _shell_ws_output_pump(websocket, shell_sessions, session.session_id, stop_event)
        )

        try:
            while True:
                message = await websocket.receive_json()
                message_type = message.get("type")
                if message_type == "input":
                    shell_sessions.write_raw(session_id=session.session_id, data=str(message.get("data", "")))
                elif message_type == "resize":
                    data = message.get("data") if isinstance(message.get("data"), dict) else {}
                    cols = _optional_int(data.get("cols"))
                    rows = _optional_int(data.get("rows"))
                    if cols is not None and rows is not None:
                        shell_sessions.resize(session_id=session.session_id, cols=cols, rows=rows)
                elif message_type == "pong":
                    continue
                elif message_type == "ping":
                    await websocket.send_json({"type": "pong", "data": message.get("data")})
        except WebSocketDisconnect:
            pass
        finally:
            stop_event.set()
            sender.cancel()
            shell_sessions.close(session.session_id)

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

    @app.post("/shell/sessions/create", response_model=ShellCreateSessionResponse)
    def shell_create_session(
        request: ShellCreateSessionRequest,
        _: None = Depends(require_api_key),
    ) -> ShellCreateSessionResponse:
        exec_dir = resolve_exec_dir(request.exec_dir or ".")
        session = shell_sessions.create_session(session_id=request.id, exec_dir=exec_dir)
        return ShellCreateSessionResponse(session_id=session.session_id, working_dir=str(session.working_dir))

    @app.get("/shell/terminal-url", response_model=ShellTerminalUrlResult)
    def shell_terminal_url(
        request: Request,
        _: None = Depends(require_api_key),
    ) -> ShellTerminalUrlResult:
        session = shell_sessions.create_session(exec_dir=resolve_exec_dir("."))
        ticket = create_ticket()
        base_url = str(request.base_url).rstrip("/")
        ws_base_url = base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        query = urlencode({"ticket": ticket["ticket"], "session_id": session.session_id})
        return ShellTerminalUrlResult(
            url=f"{ws_base_url}/shell/ws?{query}",
            session_id=session.session_id,
            expires_in=int(ticket["expires_in"]),
        )

    @app.get("/shell/sessions", response_model=ShellSessionListResult)
    def shell_list_sessions(_: None = Depends(require_api_key)) -> ShellSessionListResult:
        return ShellSessionListResult(
            sessions={
                session_id: shell_session_info(session)
                for session_id, session in shell_sessions.list().items()
            }
        )

    @app.get("/shell/sessions/stats", response_model=ShellSessionStats)
    def shell_session_stats(_: None = Depends(require_api_key)) -> ShellSessionStats:
        return ShellSessionStats(**shell_sessions.stats())

    @app.post("/shell/sessions/update", response_model=ShellUpdateSessionResult)
    def shell_update_session(
        request: ShellUpdateSessionRequest,
        _: None = Depends(require_api_key),
    ) -> ShellUpdateSessionResult:
        session = shell_sessions.update_session(
            session_id=request.id,
            no_change_timeout=request.no_change_timeout,
        )
        return ShellUpdateSessionResult(
            session_id=session.session_id,
            no_change_timeout=session.no_change_timeout,
        )

    @app.delete("/shell/sessions/{session_id}", response_model=dict[str, bool])
    def shell_close_session(session_id: str, _: None = Depends(require_api_key)) -> dict[str, bool]:
        shell_sessions.close(session_id)
        return {"success": True}

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


def _optional_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _shell_ws_output_pump(
    websocket: WebSocket,
    shell_sessions: ShellSessionManager,
    session_id: str,
    stop_event: asyncio.Event,
) -> None:
    offset = 0
    while not stop_event.is_set():
        output, offset, _ = await asyncio.to_thread(shell_sessions.wait_for_output, session_id, offset, 0.2)
        if output:
            await websocket.send_json({"type": "output", "data": output})
        await asyncio.sleep(0)

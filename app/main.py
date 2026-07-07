import asyncio
import os
import platform
import time
from contextlib import asynccontextmanager
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect

from .auth import create_ticket, require_api_key, require_http_credentials
from .bash_sessions import BashSessionManager, limit_output
from .browser_sessions import BrowserSessionManager
from .api.browser import register_browser_routes
from .api.files import register_file_routes
from .config import DEFAULT_COMMAND_TIMEOUT, MAX_COMMAND_TIMEOUT, WORKSPACE
from .file_watch import FileWatchManager
from .jupyter_sessions import JupyterSessionManager
from .mcp_tools import SandboxMcpTools
from .api.proxy import register_proxy_routes
from .core.openapi import install_openapi
from .core.paths import relative_path as _relative, resolve_exec_dir as _resolve_exec_dir
from .core.response_wrapper import install_response_wrapper
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
    JupyterCreateSessionRequest,
    JupyterCreateSessionResponse,
    JupyterExecuteRequest,
    JupyterExecuteResponse,
    JupyterInfoResponse,
    JupyterSessionListResult,
    McpCallToolResult,
    McpListToolsResult,
    SandboxContext,
    ShellCreateSessionRequest,
    ShellCreateSessionResponse,
    ShellExecRequest,
    ShellExecResult,
    ShellKillRequest,
    ShellKillResult,
    ShellSessionStats,
    ShellSessionListResult,
    ShellTerminalUrlResult,
    ShellUpdateSessionRequest,
    ShellUpdateSessionResult,
    TicketCreateResult,
    ShellViewRequest,
    ShellViewResult,
    ShellWaitRequest,
    ShellWaitResult,
    ShellWriteRequest,
    ShellWriteResult,
)
from .security import ensure_workspace
from .shell_sessions import ShellSessionManager, shell_result, shell_session_info

bash_sessions = BashSessionManager()
shell_sessions = ShellSessionManager()
file_watchers = FileWatchManager()
jupyter_sessions = JupyterSessionManager()
browser_sessions = BrowserSessionManager(download_dir=lambda: WORKSPACE / "Downloads")
mcp_tools = SandboxMcpTools(
    shell_sessions=shell_sessions,
    jupyter_sessions=jupyter_sessions,
    resolve_exec_dir=lambda path: _resolve_exec_dir(path or "."),
    relative_path=lambda path: _relative(path),
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_workspace()
    try:
        yield
    finally:
        browser_sessions.close()
        jupyter_sessions.delete_all()


app = FastAPI(
    title="Mini Agent Sandbox",
    description="A minimal Docker-backed sandbox API for learning agent infrastructure.",
    version="0.1.0",
    lifespan=lifespan,
)
install_response_wrapper(app)
register_browser_routes(app, browser_sessions)
register_file_routes(app, file_watchers)
register_proxy_routes(app)
install_openapi(app)


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


@app.post("/tickets", response_model=TicketCreateResult)
def create_auth_ticket(_: None = Depends(require_api_key)) -> TicketCreateResult:
    return TicketCreateResult(**create_ticket())


@app.get("/mcp/servers", response_model=list[str])
def mcp_list_servers(
    include_hidden: bool = False,
    _: None = Depends(require_api_key),
) -> list[str]:
    return mcp_tools.list_servers()


@app.get("/mcp/{server_name}/tools", response_model=McpListToolsResult)
def mcp_list_tools(server_name: str, _: None = Depends(require_api_key)) -> McpListToolsResult:
    return mcp_tools.list_tools(server_name)


@app.post("/mcp/{server_name}/tools/{tool_name}", response_model=McpCallToolResult)
def mcp_call_tool(
    server_name: str,
    tool_name: str,
    arguments: dict,
    _: None = Depends(require_api_key),
) -> McpCallToolResult:
    return mcp_tools.call_tool(server_name, tool_name, arguments)


@app.get("/jupyter/info", response_model=JupyterInfoResponse)
def jupyter_info(_: None = Depends(require_api_key)) -> JupyterInfoResponse:
    return JupyterInfoResponse(**jupyter_sessions.info())


@app.post("/jupyter/sessions/create", response_model=JupyterCreateSessionResponse)
def jupyter_create_session(
    request: JupyterCreateSessionRequest,
    _: None = Depends(require_api_key),
) -> JupyterCreateSessionResponse:
    cwd = _resolve_exec_dir(request.cwd or ".")
    session = jupyter_sessions.create_session(
        session_id=request.session_id,
        kernel_name=request.kernel_name,
        cwd=cwd,
    )
    return JupyterCreateSessionResponse(
        session_id=session.session_id,
        kernel_name=session.kernel_name,
        message="Jupyter session created",
    )


@app.get("/jupyter/sessions", response_model=JupyterSessionListResult)
def jupyter_list_sessions(_: None = Depends(require_api_key)) -> JupyterSessionListResult:
    return JupyterSessionListResult(
        sessions={
            session_id: jupyter_sessions.session_info(session)
            for session_id, session in jupyter_sessions.list().items()
        }
    )


@app.delete("/jupyter/sessions", response_model=dict[str, bool])
def jupyter_delete_sessions(_: None = Depends(require_api_key)) -> dict[str, bool]:
    jupyter_sessions.delete_all()
    return {"success": True}


@app.delete("/jupyter/sessions/{session_id}", response_model=dict[str, bool])
def jupyter_delete_session(session_id: str, _: None = Depends(require_api_key)) -> dict[str, bool]:
    jupyter_sessions.delete_session(session_id)
    return {"success": True}


@app.post("/jupyter/execute", response_model=JupyterExecuteResponse)
def jupyter_execute(
    request: JupyterExecuteRequest,
    _: None = Depends(require_api_key),
) -> JupyterExecuteResponse:
    cwd = _resolve_exec_dir(request.cwd or ".") if request.cwd is not None or request.session_id is None else None
    return JupyterExecuteResponse(**jupyter_sessions.execute(
        code=request.code,
        timeout=request.timeout or 30,
        session_id=request.session_id,
        kernel_name=request.kernel_name,
        cwd=cwd,
    ))


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

    exec_dir = _resolve_exec_dir(websocket.query_params.get("exec_dir") or ".")
    session = shell_sessions.start_interactive(
        session_id=websocket.query_params.get("session_id"),
        exec_dir=exec_dir,
        cols=_optional_int(websocket.query_params.get("cols")),
        rows=_optional_int(websocket.query_params.get("rows")),
    )
    await websocket.accept()
    await websocket.send_json({"type": "session", "session_id": session.session_id})

    stop_event = asyncio.Event()
    sender = asyncio.create_task(_shell_ws_output_pump(websocket, session.session_id, stop_event))

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
        exec_dir = _resolve_exec_dir(request.exec_dir)
    elif request.id is None:
        exec_dir = _resolve_exec_dir(".")
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
    exec_dir = _resolve_exec_dir(request.exec_dir or ".")
    session = shell_sessions.create_session(session_id=request.id, exec_dir=exec_dir)
    return ShellCreateSessionResponse(session_id=session.session_id, working_dir=str(session.working_dir))


@app.get("/shell/terminal-url", response_model=ShellTerminalUrlResult)
def shell_terminal_url(
    request: Request,
    _: None = Depends(require_api_key),
) -> ShellTerminalUrlResult:
    session = shell_sessions.create_session(exec_dir=_resolve_exec_dir("."))
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


def _optional_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _shell_ws_output_pump(websocket: WebSocket, session_id: str, stop_event: asyncio.Event) -> None:
    offset = 0
    while not stop_event.is_set():
        output, offset, _ = await asyncio.to_thread(shell_sessions.wait_for_output, session_id, offset, 0.2)
        if output:
            await websocket.send_json({"type": "output", "data": output})
        await asyncio.sleep(0)


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

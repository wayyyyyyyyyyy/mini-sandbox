import asyncio
import base64
import fnmatch
import json
import os
import platform
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .auth import create_ticket, require_api_key, require_http_credentials
from .bash_sessions import BashSessionManager, limit_output
from .browser_sessions import BrowserSessionManager
from .config import DEFAULT_COMMAND_TIMEOUT, MAX_COMMAND_TIMEOUT, WORKSPACE
from .file_watch import FileWatchManager
from .jupyter_sessions import JupyterSessionManager
from .mcp_tools import SandboxMcpTools
from .openapi import install_openapi
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
    BrowserActivateTabResult,
    BrowserCloseTabResult,
    BrowserCreateTabRequest,
    BrowserCreateTabResult,
    BrowserEvaluateRequest,
    BrowserEvaluateResult,
    BrowserInfoResult,
    BrowserInteractionResult,
    BrowserNetworkExportHarRequest,
    BrowserNetworkExportHarResult,
    BrowserNetworkHeadersRequest,
    BrowserNetworkHeadersResult,
    BrowserNetworkRequestsResult,
    BrowserNetworkRouteRemoveRequest,
    BrowserNetworkRouteRemoveResult,
    BrowserNetworkRouteRequest,
    BrowserNetworkRouteResult,
    BrowserNetworkScopedHeadersRequest,
    BrowserNetworkScopedHeadersResult,
    BrowserNavigateRequest,
    BrowserNavigateResult,
    BrowserRestartRequest,
    BrowserRestartResult,
    BrowserSelectorRequest,
    BrowserStatePathRequest,
    BrowserStateResult,
    BrowserTabListResult,
    BrowserTextInputRequest,
    BrowserUploadFileRequest,
    BrowserUploadFileResult,
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
    FileWatchCreateRequest,
    FileWatchCreateResult,
    FileWatchDeleteResult,
    FileWatchPollRequest,
    FileWatchPollResult,
    FileWatchWaitRequest,
    FileWatchWaitResult,
    JupyterCreateSessionRequest,
    JupyterCreateSessionResponse,
    JupyterExecuteRequest,
    JupyterExecuteResponse,
    JupyterInfoResponse,
    JupyterSessionListResult,
    McpCallToolResult,
    McpListToolsResult,
    SandboxResponse,
    FileWriteRequest,
    FileWriteResult,
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
from .security import ensure_file_size_allowed, ensure_workspace, resolve_workspace_path
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
install_openapi(app)


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


@app.api_route(
    "/proxy/{port}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
@app.api_route(
    "/proxy/{port}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
async def proxy_http(
    port: int,
    request: Request,
    path: str = "",
    _: None = Depends(require_api_key),
) -> Response:
    if port < 1 or port > 65535:
        raise HTTPException(status_code=422, detail="proxy port must be between 1 and 65535")

    target_url = f"http://127.0.0.1:{port}/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            upstream = await client.request(
                request.method,
                target_url,
                content=await request.body(),
                headers=_proxy_request_headers(request.headers, port),
            )
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=502, detail=f"proxy upstream unavailable: {port}") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail=f"proxy upstream timed out: {port}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"proxy upstream error: {exc}") from exc

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_proxy_response_headers(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )


@app.post("/tickets", response_model=TicketCreateResult)
def create_auth_ticket(_: None = Depends(require_api_key)) -> TicketCreateResult:
    return TicketCreateResult(**create_ticket())


@app.get("/browser/info", response_model=BrowserInfoResult)
def browser_info(_: None = Depends(require_api_key)) -> BrowserInfoResult:
    return BrowserInfoResult(**browser_sessions.info())


@app.post("/browser/page/navigate", response_model=BrowserNavigateResult)
def browser_navigate(
    request: BrowserNavigateRequest,
    _: None = Depends(require_api_key),
) -> BrowserNavigateResult:
    return BrowserNavigateResult(**browser_sessions.navigate(
        url=request.url,
        wait_until=request.wait_until,
        timeout=request.timeout,
    ))


@app.get("/browser/page/html", response_model=str)
def browser_html(
    outer: bool = False,
    _: None = Depends(require_api_key),
) -> str:
    return browser_sessions.html(outer=outer)


@app.get("/browser/page/text", response_model=str)
def browser_text(_: None = Depends(require_api_key)) -> str:
    return browser_sessions.text()


@app.post("/browser/page/evaluate", response_model=BrowserEvaluateResult)
def browser_evaluate(
    request: BrowserEvaluateRequest,
    _: None = Depends(require_api_key),
) -> BrowserEvaluateResult:
    return BrowserEvaluateResult(**browser_sessions.evaluate(request.script))


@app.post("/browser/page/wait_for_selector", response_model=BrowserInteractionResult)
def browser_wait_for_selector(
    request: BrowserSelectorRequest,
    _: None = Depends(require_api_key),
) -> BrowserInteractionResult:
    return BrowserInteractionResult(**browser_sessions.wait_for_selector(
        selector=request.selector,
        timeout=request.timeout,
    ))


@app.post("/browser/page/click", response_model=BrowserInteractionResult)
def browser_click(
    request: BrowserSelectorRequest,
    _: None = Depends(require_api_key),
) -> BrowserInteractionResult:
    return BrowserInteractionResult(**browser_sessions.click(
        selector=request.selector,
        timeout=request.timeout,
    ))


@app.post("/browser/page/type", response_model=BrowserInteractionResult)
def browser_type(
    request: BrowserTextInputRequest,
    _: None = Depends(require_api_key),
) -> BrowserInteractionResult:
    return BrowserInteractionResult(**browser_sessions.type(
        selector=request.selector,
        text=request.text,
        timeout=request.timeout,
    ))


@app.post("/browser/page/fill", response_model=BrowserInteractionResult)
def browser_fill(
    request: BrowserTextInputRequest,
    _: None = Depends(require_api_key),
) -> BrowserInteractionResult:
    return BrowserInteractionResult(**browser_sessions.fill(
        selector=request.selector,
        text=request.text,
        timeout=request.timeout,
    ))


@app.post("/browser/page/upload_file", response_model=BrowserUploadFileResult)
def browser_upload_file(
    request: BrowserUploadFileRequest,
    _: None = Depends(require_api_key),
) -> BrowserUploadFileResult:
    paths = []
    relative_files = []
    for file_path in request.files:
        path = resolve_workspace_path(file_path)
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail=f"file not found: {file_path}")
        paths.append(path)
        relative_files.append(_relative(path))
    result = browser_sessions.upload_file(
        selector=request.selector,
        files=paths,
        timeout=request.timeout,
    )
    return BrowserUploadFileResult(
        selector=result["selector"],
        files=relative_files,
        ok=result["ok"],
    )


@app.post("/browser/network/route", response_model=BrowserNetworkRouteResult)
def browser_network_add_route(
    request: BrowserNetworkRouteRequest,
    _: None = Depends(require_api_key),
) -> BrowserNetworkRouteResult:
    return BrowserNetworkRouteResult(**browser_sessions.add_network_route(
        url_pattern=request.url_pattern,
        response=request.response.model_dump() if request.response is not None else None,
        abort=request.abort,
    ))


@app.delete("/browser/network/route", response_model=BrowserNetworkRouteRemoveResult)
def browser_network_remove_route(
    request: BrowserNetworkRouteRemoveRequest,
    _: None = Depends(require_api_key),
) -> BrowserNetworkRouteRemoveResult:
    return BrowserNetworkRouteRemoveResult(**browser_sessions.remove_network_route(
        url_pattern=request.url_pattern,
    ))


@app.post("/browser/network/headers", response_model=BrowserNetworkHeadersResult)
def browser_network_headers(
    request: BrowserNetworkHeadersRequest,
    _: None = Depends(require_api_key),
) -> BrowserNetworkHeadersResult:
    return BrowserNetworkHeadersResult(**browser_sessions.set_network_headers(
        headers=request.headers,
    ))


@app.post("/browser/network/scoped_headers", response_model=BrowserNetworkScopedHeadersResult)
def browser_network_scoped_headers(
    request: BrowserNetworkScopedHeadersRequest,
    _: None = Depends(require_api_key),
) -> BrowserNetworkScopedHeadersResult:
    return BrowserNetworkScopedHeadersResult(**browser_sessions.set_network_scoped_headers(
        origin=request.origin,
        headers=request.headers,
    ))


@app.post("/browser/network/export_har", response_model=BrowserNetworkExportHarResult)
def browser_network_export_har(
    request: BrowserNetworkExportHarRequest,
    _: None = Depends(require_api_key),
) -> BrowserNetworkExportHarResult:
    path = resolve_workspace_path(request.save_path)
    result = browser_sessions.export_har()
    content = json.dumps(result["har"], indent=2, sort_keys=True).encode("utf-8")
    ensure_file_size_allowed(content)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return BrowserNetworkExportHarResult(path=_relative(path), entries=result["entries"])


@app.get("/browser/network/requests", response_model=BrowserNetworkRequestsResult)
def browser_network_requests(
    filter: str | None = None,
    limit: int = 100,
    _: None = Depends(require_api_key),
) -> BrowserNetworkRequestsResult:
    return BrowserNetworkRequestsResult(**browser_sessions.network_requests(
        filter_text=filter,
        limit=limit,
    ))


@app.post("/browser/restart", response_model=BrowserRestartResult)
def browser_restart(
    request: BrowserRestartRequest | None = None,
    _: None = Depends(require_api_key),
) -> BrowserRestartResult:
    request = request or BrowserRestartRequest()
    return BrowserRestartResult(**browser_sessions.restart(
        mode=request.mode,
        clear_routes=request.clear_routes,
    ))


@app.post("/browser/state/save", response_model=BrowserStateResult)
def browser_state_save(
    request: BrowserStatePathRequest,
    _: None = Depends(require_api_key),
) -> BrowserStateResult:
    path = resolve_workspace_path(request.path)
    state = browser_sessions.save_state()
    content = json.dumps(state, indent=2, sort_keys=True).encode("utf-8")
    ensure_file_size_allowed(content)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return BrowserStateResult(
        path=_relative(path),
        cookies=len(state["cookies"]),
        origins=len(state["origins"]),
    )


@app.post("/browser/state/load", response_model=BrowserStateResult)
def browser_state_load(
    request: BrowserStatePathRequest,
    _: None = Depends(require_api_key),
) -> BrowserStateResult:
    path = resolve_workspace_path(request.path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"browser state file not found: {request.path}")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid browser state JSON: {request.path}") from exc
    if not isinstance(state, dict):
        raise HTTPException(status_code=400, detail="browser state must be a JSON object")
    restored = browser_sessions.load_state(state)
    return BrowserStateResult(path=_relative(path), **restored)


@app.get("/browser/screenshot")
def browser_screenshot(
    format: str = "png",
    quality: int | None = None,
    _: None = Depends(require_api_key),
) -> Response:
    content, headers = browser_sessions.screenshot(image_format=format, quality=quality)
    media_type = "image/jpeg" if format in {"jpg", "jpeg"} else "image/png"
    return Response(content=content, media_type=media_type, headers=headers)


@app.get("/browser/tabs", response_model=BrowserTabListResult)
def browser_list_tabs(_: None = Depends(require_api_key)) -> BrowserTabListResult:
    return BrowserTabListResult(**browser_sessions.list_tabs())


@app.post("/browser/tabs", response_model=BrowserCreateTabResult)
def browser_create_tab(
    request: BrowserCreateTabRequest,
    _: None = Depends(require_api_key),
) -> BrowserCreateTabResult:
    return BrowserCreateTabResult(**browser_sessions.create_tab(url=request.url))


@app.put("/browser/tabs/{index}/activate", response_model=BrowserActivateTabResult)
def browser_activate_tab(index: int, _: None = Depends(require_api_key)) -> BrowserActivateTabResult:
    return BrowserActivateTabResult(**browser_sessions.activate_tab(index))


@app.delete("/browser/tabs/{index}", response_model=BrowserCloseTabResult)
def browser_close_tab(index: int, _: None = Depends(require_api_key)) -> BrowserCloseTabResult:
    return BrowserCloseTabResult(**browser_sessions.close_tab(index))


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


@app.post("/file/watch", response_model=FileWatchCreateResult)
def file_watch_create(
    request: FileWatchCreateRequest,
    _: None = Depends(require_api_key),
) -> FileWatchCreateResult:
    root = resolve_workspace_path(request.path)
    watcher = file_watchers.create(
        root=root,
        recursive=request.recursive,
        exclude=request.exclude,
        include_patterns=request.include_patterns,
    )
    return FileWatchCreateResult(
        watcher_id=watcher.watcher_id,
        path=_relative(root),
        recursive=watcher.recursive,
        cursor=0,
    )


@app.post("/file/watch/wait", response_model=FileWatchWaitResult)
def file_watch_wait(
    request: FileWatchWaitRequest,
    _: None = Depends(require_api_key),
) -> FileWatchWaitResult:
    path = resolve_workspace_path(request.path)
    return FileWatchWaitResult(**file_watchers.wait_for_file(
        path=path,
        timeout=request.timeout,
        event_types=request.event_types,
    ))


@app.get("/file/watch/{watcher_id}/events")
def file_watch_events(
    watcher_id: str,
    timeout: float = 30,
    heartbeat_interval: float = 15,
    last_event_id: str | None = None,
    last_event_id_header: str | None = Header(default=None, alias="Last-Event-ID"),
    _: None = Depends(require_api_key),
) -> StreamingResponse:
    file_watchers.ensure_exists(watcher_id)
    cursor = _file_watch_cursor_from_event_id(watcher_id, last_event_id or last_event_id_header)
    return StreamingResponse(
        _file_watch_sse_stream(
            watcher_id,
            cursor=cursor,
            timeout=max(0, min(timeout, 60)),
            heartbeat_interval=max(0.01, min(heartbeat_interval, 60)),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@app.post("/file/watch/{watcher_id}/poll", response_model=FileWatchPollResult)
def file_watch_poll(
    watcher_id: str,
    request: FileWatchPollRequest,
    _: None = Depends(require_api_key),
) -> FileWatchPollResult:
    return FileWatchPollResult(**file_watchers.poll(
        watcher_id,
        cursor=request.cursor,
        limit=request.limit,
        timeout=request.timeout,
    ))


@app.delete("/file/watch/{watcher_id}", response_model=FileWatchDeleteResult)
def file_watch_delete(watcher_id: str, _: None = Depends(require_api_key)) -> FileWatchDeleteResult:
    return FileWatchDeleteResult(**file_watchers.delete(watcher_id))


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


def _proxy_request_headers(headers, port: int) -> dict[str, str]:
    excluded = {
        "host",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
    forwarded = {
        key: value
        for key, value in headers.items()
        if key.lower() not in excluded
    }
    forwarded["host"] = f"127.0.0.1:{port}"
    return forwarded


def _proxy_response_headers(headers) -> dict[str, str]:
    excluded = {
        "connection",
        "content-encoding",
        "content-length",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in excluded
    }


def _skip_response_wrapper(path: str) -> bool:
    return path in {"/healthz", "/openapi.json"} or path.startswith(("/docs", "/redoc", "/proxy/"))


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


async def _file_watch_sse_stream(
    watcher_id: str,
    *,
    cursor: int,
    timeout: float,
    heartbeat_interval: float,
):
    yield _sse_message("watch_started", {"watcher_id": watcher_id, "cursor": cursor})
    deadline = time.monotonic() + timeout
    next_heartbeat = time.monotonic() + heartbeat_interval
    while time.monotonic() <= deadline:
        now = time.monotonic()
        wait_time = min(0.2, max(deadline - now, 0), max(next_heartbeat - now, 0))
        result = await asyncio.to_thread(
            file_watchers.poll,
            watcher_id,
            cursor=cursor,
            limit=100,
            timeout=wait_time,
        )
        cursor = result["cursor"]
        for event in result["events"]:
            yield _sse_message(
                "file_change",
                event,
                event_id=f"{watcher_id}:{event['seq']}",
            )
        if result["events"]:
            return
        if result["overflow"]:
            yield _sse_message("overflow", {"watcher_id": watcher_id, "cursor": cursor})
            return
        if time.monotonic() >= next_heartbeat:
            yield _sse_message("heartbeat", {"watcher_id": watcher_id, "cursor": cursor})
            next_heartbeat = time.monotonic() + heartbeat_interval
        await asyncio.sleep(0)


def _file_watch_cursor_from_event_id(watcher_id: str, event_id: str | None) -> int:
    if not event_id:
        return 0
    prefix = f"{watcher_id}:"
    if not event_id.startswith(prefix):
        raise HTTPException(status_code=400, detail="Last-Event-ID watcher_id mismatch")
    try:
        return int(event_id.removeprefix(prefix))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid Last-Event-ID cursor") from exc


def _sse_message(event: str, data: dict, event_id: str | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"


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

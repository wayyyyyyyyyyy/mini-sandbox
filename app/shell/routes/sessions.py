from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Request

from ...auth import create_ticket, require_api_key
from ...schemas import (
    ShellCreateSessionRequest,
    ShellCreateSessionResponse,
    ShellSessionListResult,
    ShellSessionStats,
    ShellTerminalUrlResult,
    ShellUpdateSessionRequest,
    ShellUpdateSessionResult,
)
from ...shell_sessions import ShellSessionManager, shell_session_info


def register_shell_session_routes(
    app: FastAPI,
    shell_sessions: ShellSessionManager,
    resolve_exec_dir: Callable[[str], Path],
) -> None:
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

from collections.abc import Callable
from pathlib import Path

from fastapi import Depends, FastAPI

from ..auth import require_api_key
from ..jupyter_sessions import JupyterSessionManager
from ..schemas import (
    JupyterCreateSessionRequest,
    JupyterCreateSessionResponse,
    JupyterExecuteRequest,
    JupyterExecuteResponse,
    JupyterInfoResponse,
    JupyterSessionListResult,
)


def register_jupyter_routes(
    app: FastAPI,
    jupyter_sessions: JupyterSessionManager,
    resolve_exec_dir: Callable[[str], Path],
) -> None:
    @app.get("/jupyter/info", response_model=JupyterInfoResponse)
    def jupyter_info(_: None = Depends(require_api_key)) -> JupyterInfoResponse:
        return JupyterInfoResponse(**jupyter_sessions.info())

    @app.post("/jupyter/sessions/create", response_model=JupyterCreateSessionResponse)
    def jupyter_create_session(
        request: JupyterCreateSessionRequest,
        _: None = Depends(require_api_key),
    ) -> JupyterCreateSessionResponse:
        cwd = resolve_exec_dir(request.cwd or ".")
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
        cwd = resolve_exec_dir(request.cwd or ".") if request.cwd is not None or request.session_id is None else None
        return JupyterExecuteResponse(**jupyter_sessions.execute(
            code=request.code,
            timeout=request.timeout or 30,
            session_id=request.session_id,
            kernel_name=request.kernel_name,
            cwd=cwd,
        ))

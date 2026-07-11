import os
import platform
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from .auth import create_ticket, require_api_key
from .api.bash import register_bash_routes
from .browser import BrowserSessionManager
from .browser.routes import register_browser_routes
from .files import register_file_routes
from .api.jupyter import register_jupyter_routes
from .api.mcp import register_mcp_routes
from .api.ports import register_port_routes
from .api.shell import register_shell_routes
from .bash_sessions import BashSessionManager
from .config import WORKSPACE
from .files.watch import FileWatchManager
from .jupyter_sessions import JupyterSessionManager
from .mcp_tools import SandboxMcpTools
from .api.proxy import register_proxy_routes
from .core.openapi import install_openapi
from .core.paths import relative_path as _relative, resolve_exec_dir as _resolve_exec_dir
from .core.response_wrapper import install_response_wrapper
from .schemas import (
    SandboxContext,
    TicketCreateResult,
)
from .security import ensure_workspace
from .shell_sessions import ShellSessionManager

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
    browser_sessions=browser_sessions,
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
register_bash_routes(app, bash_sessions, _resolve_exec_dir)
register_browser_routes(app, browser_sessions)
register_file_routes(app, file_watchers)
register_jupyter_routes(app, jupyter_sessions, _resolve_exec_dir)
register_mcp_routes(app, mcp_tools)
register_port_routes(app)
register_proxy_routes(app)
register_shell_routes(app, shell_sessions, _resolve_exec_dir)
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

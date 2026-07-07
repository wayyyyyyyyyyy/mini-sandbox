from fastapi import Depends, FastAPI

from ..auth import require_api_key
from ..schemas import BrowserInfoResult, BrowserRestartRequest, BrowserRestartResult
from .manager import BrowserSessionManager


def register_browser_lifecycle_routes(app: FastAPI, browser_sessions: BrowserSessionManager) -> None:
    @app.get("/browser/info", response_model=BrowserInfoResult)
    def browser_info(_: None = Depends(require_api_key)) -> BrowserInfoResult:
        return BrowserInfoResult(**browser_sessions.info())

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

from fastapi import Depends, FastAPI

from ..auth import require_api_key
from ..schemas import (
    BrowserActivateTabResult,
    BrowserCloseTabResult,
    BrowserCreateTabRequest,
    BrowserCreateTabResult,
    BrowserTabListResult,
)
from .manager import BrowserSessionManager


def register_browser_tabs_routes(app: FastAPI, browser_sessions: BrowserSessionManager) -> None:
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

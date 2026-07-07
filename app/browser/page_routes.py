from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException

from ..auth import require_api_key
from ..core.paths import relative_path as _relative
from ..schemas import (
    BrowserEvaluateRequest,
    BrowserEvaluateResult,
    BrowserInteractionResult,
    BrowserNavigateRequest,
    BrowserNavigateResult,
    BrowserSelectorRequest,
    BrowserTextInputRequest,
    BrowserUploadFileRequest,
    BrowserUploadFileResult,
)
from ..security import resolve_workspace_path
from .manager import BrowserSessionManager


def register_browser_page_routes(app: FastAPI, browser_sessions: BrowserSessionManager) -> None:
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
        paths: list[Path] = []
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

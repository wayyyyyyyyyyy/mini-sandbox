import json
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import Response

from ..auth import require_api_key
from .manager import BrowserSessionManager
from ..core.paths import relative_path as _relative
from ..schemas import (
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
)
from ..security import ensure_file_size_allowed, resolve_workspace_path


def register_browser_routes(app: FastAPI, browser_sessions: BrowserSessionManager) -> None:
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

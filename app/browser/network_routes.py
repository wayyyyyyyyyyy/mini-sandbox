import json

from fastapi import Depends, FastAPI

from ..auth import require_api_key
from ..core.paths import relative_path as _relative
from ..schemas import (
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
)
from ..security import ensure_file_size_allowed, resolve_workspace_path
from .manager import BrowserSessionManager


def register_browser_network_routes(app: FastAPI, browser_sessions: BrowserSessionManager) -> None:
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

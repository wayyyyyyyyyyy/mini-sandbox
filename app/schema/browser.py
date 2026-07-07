from typing import Any, Literal

from pydantic import BaseModel, Field


class BrowserNavigateRequest(BaseModel):
    url: str = Field(min_length=1)
    wait_until: Literal["load", "domcontentloaded", "networkidle", "commit"] = "load"
    timeout: int = Field(default=30000, ge=1000, le=120000)


class BrowserNavigateResult(BaseModel):
    url: str
    title: str
    status: int | None = None


class BrowserEvaluateRequest(BaseModel):
    script: str = Field(min_length=1)


class BrowserEvaluateResult(BaseModel):
    result: Any = None


class BrowserSelectorRequest(BaseModel):
    selector: str = Field(min_length=1)
    timeout: int = Field(default=30000, ge=0, le=120000)


class BrowserTextInputRequest(BrowserSelectorRequest):
    text: str


class BrowserInteractionResult(BaseModel):
    selector: str
    ok: bool


class BrowserUploadFileRequest(BrowserSelectorRequest):
    files: list[str] = Field(min_length=1)


class BrowserUploadFileResult(BaseModel):
    selector: str
    files: list[str]
    ok: bool


class BrowserRouteResponse(BaseModel):
    status: int = Field(default=200, ge=100, le=599)
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = ""
    content_type: str = "text/plain"


class BrowserNetworkRouteRequest(BaseModel):
    url_pattern: str = Field(min_length=1)
    response: BrowserRouteResponse | None = None
    abort: bool = False


class BrowserNetworkRouteRemoveRequest(BaseModel):
    url_pattern: str = Field(min_length=1)


class BrowserNetworkRouteResult(BaseModel):
    url_pattern: str
    active: bool
    abort: bool


class BrowserNetworkRouteRemoveResult(BaseModel):
    url_pattern: str
    removed: bool


class BrowserNetworkHeadersRequest(BaseModel):
    headers: dict[str, str]


class BrowserNetworkHeadersResult(BaseModel):
    headers: dict[str, str]


class BrowserNetworkScopedHeadersRequest(BaseModel):
    origin: str = Field(min_length=1)
    headers: dict[str, str]


class BrowserNetworkScopedHeadersResult(BaseModel):
    origin: str
    headers: dict[str, str]


class BrowserNetworkExportHarRequest(BaseModel):
    save_path: str = Field(min_length=1)


class BrowserNetworkExportHarResult(BaseModel):
    path: str
    entries: int


class BrowserNetworkRequestEntry(BaseModel):
    request_id: str
    url: str
    method: str
    resource_type: str
    timestamp: float | None = None
    status: int | None = None
    failed: bool = False
    error_text: str | None = None
    mime_type: str | None = None


class BrowserNetworkRequestsResult(BaseModel):
    requests: list[BrowserNetworkRequestEntry]


class BrowserRestartRequest(BaseModel):
    mode: Literal["soft", "hard"] = "hard"
    url_blocklist: list[str] | None = None
    url_allowlist: list[str] | None = None
    allow_file_selection_dialogs: bool | None = None
    locale: str | None = None
    clear_routes: bool = True


class BrowserRestartResult(BaseModel):
    mode: Literal["soft", "hard"]
    restarted: bool
    page_count: int
    current_url: str
    routes_cleared: bool


class BrowserStatePathRequest(BaseModel):
    path: str = Field(min_length=1)


class BrowserStateResult(BaseModel):
    path: str
    cookies: int
    origins: int


class BrowserViewport(BaseModel):
    width: int
    height: int


class BrowserInfoResult(BaseModel):
    browser: str
    headless: bool
    viewport: BrowserViewport
    page_count: int
    current_url: str


class BrowserCreateTabRequest(BaseModel):
    url: str | None = None


class BrowserTabInfo(BaseModel):
    index: int
    url: str
    title: str
    active: bool


class BrowserCreateTabResult(BrowserTabInfo):
    pass


class BrowserTabListResult(BaseModel):
    tabs: list[BrowserTabInfo]


class BrowserActivateTabResult(BaseModel):
    active_index: int


class BrowserCloseTabResult(BaseModel):
    closed: bool
    active_index: int

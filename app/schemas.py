from typing import Any, Literal

from pydantic import BaseModel, Field


class SandboxContext(BaseModel):
    workspace: str
    user: str
    cwd: str
    python_version: str


class SandboxResponse(BaseModel):
    success: bool
    message: str
    data: Any = None
    hint: str | None = None


class TicketCreateResult(BaseModel):
    ticket: str
    expires_in: int


class ShellCreateSessionRequest(BaseModel):
    id: str | None = None
    exec_dir: str | None = None


class ShellCreateSessionResponse(BaseModel):
    session_id: str
    working_dir: str


class ShellExecRequest(BaseModel):
    id: str | None = None
    exec_dir: str | None = None
    command: str = Field(min_length=1)
    async_mode: bool = False
    timeout: float | None = Field(default=None, gt=0)
    hard_timeout: float | None = Field(default=None, gt=0)


class ShellExecResult(BaseModel):
    session_id: str
    command: str | None
    status: Literal["ready", "running", "completed", "killed", "closed"]
    output: str
    exit_code: int | None = None


class ShellViewRequest(BaseModel):
    id: str


class ShellViewResult(ShellExecResult):
    pass


class ShellWaitRequest(BaseModel):
    id: str
    seconds: float | None = Field(default=None, ge=0)


class ShellWaitResult(BaseModel):
    status: Literal["ready", "running", "completed", "killed", "closed"]


class ShellWriteRequest(BaseModel):
    id: str
    input: str
    press_enter: bool


class ShellWriteResult(BaseModel):
    status: Literal["ready", "running", "completed", "killed", "closed"]


class ShellKillRequest(BaseModel):
    id: str


class ShellKillResult(BaseModel):
    status: Literal["ready", "running", "completed", "killed", "closed"]
    exit_code: int | None = None
    returncode: int | None = None


class ShellUpdateSessionRequest(BaseModel):
    id: str
    no_change_timeout: int | None = Field(default=None, ge=0)


class ShellUpdateSessionResult(BaseModel):
    session_id: str
    no_change_timeout: int | None = None


class ShellTerminalUrlResult(BaseModel):
    url: str
    session_id: str
    expires_in: int


class ShellSessionStats(BaseModel):
    total_sessions: int
    active_sessions: int
    idle_sessions: int
    max_sessions: int
    session_timeout: int
    usage_ratio: float


class ShellSessionInfo(BaseModel):
    working_dir: str
    created_at: str
    last_used_at: str
    age_seconds: int
    status: str
    current_command: str | None = None
    no_change_timeout: int | None = None


class ShellSessionListResult(BaseModel):
    sessions: dict[str, ShellSessionInfo]


class BashExecRequest(BaseModel):
    session_id: str | None = None
    command: str = Field(min_length=1)
    exec_dir: str | None = None
    timeout: float | None = Field(default=None, gt=0)
    hard_timeout: float | None = Field(default=None, gt=0)
    async_mode: bool = False
    max_output_length: int | None = Field(default=None, ge=0)
    env: dict[str, str] = Field(default_factory=dict)


class BashCommandResult(BaseModel):
    session_id: str
    command_id: str
    command: str
    status: Literal["running", "completed", "timed_out", "killed"]
    stdout: str
    stderr: str
    stdout_offset: int
    stderr_offset: int
    stdout_bytes: int
    stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    exit_code: int | None


class BashCommandInfo(BaseModel):
    command_id: str
    command: str
    status: Literal["running", "completed", "timed_out", "killed"]
    exit_code: int | None = None


class BashOutputResult(BashCommandResult):
    offset: int
    command_info: BashCommandInfo


class BashSessionInfo(BaseModel):
    session_id: str
    status: Literal["ready", "closed"]
    working_dir: str
    created_at: str
    last_used_at: str
    current_command: str | None = None
    command_count: int = 0
    command_id: str | None = None
    command: str | None = None
    stdout_offset: int | None = None
    stderr_offset: int | None = None
    exit_code: int | None = None
    duration_ms: int | None = None


class BashSessionListResult(BaseModel):
    sessions: list[BashSessionInfo]


class JupyterCreateSessionRequest(BaseModel):
    session_id: str | None = None
    kernel_name: str | None = None
    cwd: str | None = None


class JupyterCreateSessionResponse(BaseModel):
    session_id: str
    kernel_name: str
    message: str


class JupyterExecuteRequest(BaseModel):
    code: str = Field(min_length=1)
    timeout: int | None = Field(default=30, ge=1, le=300)
    kernel_name: str | None = None
    session_id: str | None = None
    cwd: str | None = None


class JupyterOutput(BaseModel):
    output_type: str
    name: str | None = None
    text: str | None = None
    data: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    execution_count: int | None = None
    ename: str | None = None
    evalue: str | None = None
    traceback: list[str] | None = None


class JupyterExecuteResponse(BaseModel):
    kernel_name: str
    session_id: str | None = None
    status: Literal["ok", "error", "timeout"]
    execution_count: int | None = None
    outputs: list[JupyterOutput]
    code: str
    msg_id: str | None = None


class JupyterInfoResponse(BaseModel):
    default_kernel: str
    available_kernels: list[str]
    active_sessions: int
    session_timeout_seconds: int
    max_sessions: int
    description: str
    kernel_detection: str


class JupyterSessionInfo(BaseModel):
    kernel_name: str
    last_used: str
    age_seconds: int


class JupyterSessionListResult(BaseModel):
    sessions: dict[str, JupyterSessionInfo]


class McpToolInfo(BaseModel):
    name: str
    description: str
    inputSchema: dict[str, Any]


class McpListToolsResult(BaseModel):
    tools: list[McpToolInfo]


class McpContentItem(BaseModel):
    type: Literal["text", "json"]
    text: str | None = None
    data: Any = None


class McpCallToolResult(BaseModel):
    content: list[McpContentItem]
    isError: bool = False


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


class BashSessionCreateRequest(BaseModel):
    session_id: str | None = None
    exec_dir: str | None = None
    snapshot_path: str | None = None


class BashOutputRequest(BaseModel):
    session_id: str
    command_id: str | None = None
    offset: int = Field(default=0, ge=0)
    stderr_offset: int = Field(default=0, ge=0)
    wait: bool = False
    wait_timeout: float = Field(default=30, ge=0)


class BashKillRequest(BaseModel):
    session_id: str
    signal: str = "SIGTERM"


class BashWriteRequest(BaseModel):
    session_id: str
    command_id: str | None = None
    input: str


class FileReadRequest(BaseModel):
    path: str
    start_line: int | None = Field(default=None, ge=0)
    end_line: int | None = Field(default=None, ge=0)


class FileReadResult(BaseModel):
    path: str
    content: str
    bytes: int
    line_count: int | None = None


class FileWriteRequest(BaseModel):
    path: str
    content: str
    create_parent: bool = True
    encoding: Literal["utf-8", "base64", "raw"] = "utf-8"
    append: bool = False
    leading_newline: bool = False
    trailing_newline: bool = False


class FileWriteResult(BaseModel):
    path: str
    bytes: int


class FileWatchCreateRequest(BaseModel):
    path: str = "."
    recursive: bool = True
    exclude: list[str] = Field(default_factory=list)
    include_patterns: list[str] = Field(default_factory=list)


class FileWatchCreateResult(BaseModel):
    watcher_id: str
    path: str
    recursive: bool
    cursor: int


class FileWatchPollRequest(BaseModel):
    cursor: int = Field(default=0, ge=0)
    limit: int = Field(default=100, gt=0)
    timeout: float = Field(default=0, ge=0, le=60)


class FileWatchEvent(BaseModel):
    seq: int
    type: Literal["created", "modified", "deleted", "create", "write", "remove", "rename", "chmod"]
    path: str
    relative_path: str
    is_dir: bool
    timestamp: float
    mtime: float | None = None
    size: int


class FileWatchPollResult(BaseModel):
    watcher_id: str
    cursor: int
    events: list[FileWatchEvent]
    overflow: bool = False


class FileWatchWaitRequest(BaseModel):
    path: str
    timeout: float = Field(default=30, ge=0, le=300)
    event_types: list[Literal["create", "write", "remove", "rename", "chmod"]] = Field(
        default_factory=lambda: ["create", "write", "remove", "rename", "chmod"]
    )


class FileWatchWaitResult(BaseModel):
    event: FileWatchEvent | None = None


class FileWatchDeleteResult(BaseModel):
    watcher_id: str
    closed: bool


class FileReplaceRequest(BaseModel):
    path: str
    old_str: str = Field(min_length=1)
    new_str: str
    all: bool = False
    count: int | None = Field(default=None, gt=0)


class FileReplaceResult(BaseModel):
    path: str
    replaced: int
    changed: bool


class FileListRequest(BaseModel):
    path: str = "."
    recursive: bool = False
    show_hidden: bool = True
    include_size: bool = True


class FileInfo(BaseModel):
    path: str
    kind: Literal["file", "directory", "other"]
    bytes: int


class FileListResult(BaseModel):
    path: str
    entries: list[FileInfo]


class FileFindRequest(BaseModel):
    path: str = "."
    glob: str = "*"
    include_hidden: bool = True
    max_results: int = Field(default=100, gt=0)


class FileFindResult(BaseModel):
    path: str
    glob: str
    files: list[str]


class FileGlobRequest(BaseModel):
    path: str = "."
    pattern: str = "*"
    exclude: list[str] = Field(default_factory=list)
    include_hidden: bool = True
    files_only: bool = False
    include_metadata: bool = False
    max_results: int = Field(default=100, gt=0)
    sort_by: Literal["path", "name"] = "path"


class FileGlobResult(BaseModel):
    path: str
    pattern: str
    matches: list[str]
    entries: list[FileInfo] = Field(default_factory=list)


class FileSearchRequest(BaseModel):
    path: str
    regex: str
    case_insensitive: bool = False
    max_results: int = Field(default=100, gt=0)


class FileSearchMatch(BaseModel):
    line: int
    text: str
    match: str


class FileSearchResult(BaseModel):
    path: str
    regex: str
    matches: list[FileSearchMatch]


class FileGrepRequest(BaseModel):
    path: str = "."
    pattern: str
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    case_insensitive: bool = False
    max_results: int = Field(default=100, gt=0)


class FileGrepMatch(BaseModel):
    path: str
    line: int
    text: str
    match: str


class FileGrepResult(BaseModel):
    path: str
    pattern: str
    matches: list[FileGrepMatch]

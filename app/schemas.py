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


class ShellExecRequest(BaseModel):
    command: str = Field(min_length=1)
    exec_dir: str | None = None
    timeout: float | None = Field(default=None, gt=0)
    env: dict[str, str] = Field(default_factory=dict)


class ShellExecResult(BaseModel):
    command: str
    status: Literal["completed", "timed_out"]
    stdout: str
    stderr: str
    stdout_bytes: int
    stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    exit_code: int | None
    duration_ms: int


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

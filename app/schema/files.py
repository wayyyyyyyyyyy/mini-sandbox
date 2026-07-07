from typing import Literal

from pydantic import BaseModel, Field


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

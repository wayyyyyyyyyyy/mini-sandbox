from typing import Literal

from pydantic import BaseModel, Field


class SandboxContext(BaseModel):
    workspace: str
    user: str
    cwd: str
    python_version: str


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
    command: str = Field(min_length=1)
    exec_dir: str | None = None
    hard_timeout: float | None = Field(default=None, gt=0)
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


class BashSessionInfo(BaseModel):
    session_id: str
    command_id: str
    command: str
    status: Literal["running", "completed", "timed_out", "killed"]
    stdout_offset: int
    stderr_offset: int
    exit_code: int | None
    duration_ms: int


class BashSessionListResult(BaseModel):
    sessions: list[BashSessionInfo]


class BashOutputRequest(BaseModel):
    session_id: str
    offset: int = Field(default=0, ge=0)
    stderr_offset: int = Field(default=0, ge=0)


class BashKillRequest(BaseModel):
    session_id: str


class BashWriteRequest(BaseModel):
    session_id: str
    input: str


class FileReadRequest(BaseModel):
    path: str


class FileReadResult(BaseModel):
    path: str
    content: str
    bytes: int


class FileWriteRequest(BaseModel):
    path: str
    content: str
    create_parent: bool = True


class FileWriteResult(BaseModel):
    path: str
    bytes: int


class FileListRequest(BaseModel):
    path: str = "."


class FileInfo(BaseModel):
    path: str
    kind: Literal["file", "directory", "other"]
    bytes: int


class FileListResult(BaseModel):
    path: str
    entries: list[FileInfo]

from typing import Literal

from pydantic import BaseModel, Field


BashCommandStatus = Literal["running", "completed", "timed_out", "killed"]


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
    status: BashCommandStatus
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
    status: BashCommandStatus
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

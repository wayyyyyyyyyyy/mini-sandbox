from typing import Literal

from pydantic import BaseModel, Field


ShellStatus = Literal["ready", "running", "completed", "killed", "closed"]


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
    status: ShellStatus
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
    status: ShellStatus


class ShellWriteRequest(BaseModel):
    id: str
    input: str
    press_enter: bool


class ShellWriteResult(BaseModel):
    status: ShellStatus


class ShellKillRequest(BaseModel):
    id: str


class ShellKillResult(BaseModel):
    status: ShellStatus
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

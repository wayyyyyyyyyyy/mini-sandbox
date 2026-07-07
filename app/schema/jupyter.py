from typing import Any, Literal

from pydantic import BaseModel, Field


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

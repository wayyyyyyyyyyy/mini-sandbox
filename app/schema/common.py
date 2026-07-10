from typing import Any

from pydantic import BaseModel


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


class PortInfo(BaseModel):
    port: int
    host: str
    protocol: str = "tcp"
    pid: int | None = None
    process_name: str | None = None
    proxy_url: str


class PortListResult(BaseModel):
    ports: list[PortInfo]

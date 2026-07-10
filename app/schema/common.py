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

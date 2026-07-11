from typing import Any, Literal

from pydantic import BaseModel


class McpToolInfo(BaseModel):
    name: str
    description: str
    inputSchema: dict[str, Any]


class McpListToolsResult(BaseModel):
    tools: list[McpToolInfo]


class McpContentItem(BaseModel):
    type: Literal["text", "json", "image"]
    text: str | None = None
    data: Any = None
    mimeType: str | None = None


class McpCallToolResult(BaseModel):
    content: list[McpContentItem]
    isError: bool = False

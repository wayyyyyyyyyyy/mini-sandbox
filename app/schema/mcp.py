from typing import Any, Literal

from pydantic import BaseModel


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

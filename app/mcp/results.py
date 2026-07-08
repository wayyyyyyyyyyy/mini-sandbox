from __future__ import annotations

from typing import Any

from ..schemas import McpCallToolResult, McpContentItem


def json_result(data: dict[str, Any]) -> McpCallToolResult:
    return McpCallToolResult(content=[McpContentItem(type="json", data=data)])

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..schemas import McpCallToolResult

ToolHandler = Callable[[dict[str, Any]], McpCallToolResult]


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler

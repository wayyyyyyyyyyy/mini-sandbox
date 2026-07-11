from __future__ import annotations

from typing import Any

from ...browser.manager import BrowserSessionManager
from ...schemas import McpCallToolResult
from ..models import McpTool
from ..results import json_result
from ..validators import optional_int_range, required_string


class BrowserInteractionMcpTools:
    def __init__(self, *, browser_sessions: BrowserSessionManager) -> None:
        self.browser_sessions = browser_sessions

    def tools(self) -> dict[str, McpTool]:
        return {
            "browser_click": McpTool(
                name="browser_click",
                description="Click a selector in the active sandbox browser tab.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string"},
                        "timeout": {"type": "integer", "minimum": 0, "maximum": 120000},
                    },
                    "required": ["selector"],
                },
                handler=self.browser_click,
            ),
        }

    def browser_click(self, arguments: dict[str, Any]) -> McpCallToolResult:
        selector = required_string(arguments, "selector")
        timeout = optional_int_range(arguments, "timeout", default=30000, minimum=0, maximum=120000)
        return json_result(self.browser_sessions.click(selector=selector, timeout=timeout))

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from ..browser.manager import BrowserSessionManager
from ..schemas import McpCallToolResult
from .models import McpTool
from .results import json_result
from .validators import optional_int_range, optional_string, required_string

_WAIT_UNTIL_VALUES = {"load", "domcontentloaded", "networkidle", "commit"}


class BrowserMcpTools:
    def __init__(self, *, browser_sessions: BrowserSessionManager) -> None:
        self.browser_sessions = browser_sessions

    def tools(self) -> dict[str, McpTool]:
        return {
            "browser_navigate": McpTool(
                name="browser_navigate",
                description="Navigate the active sandbox browser tab to an allowed URL.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "wait_until": {
                            "type": "string",
                            "enum": sorted(_WAIT_UNTIL_VALUES),
                        },
                        "timeout": {"type": "integer", "minimum": 1000, "maximum": 120000},
                    },
                    "required": ["url"],
                },
                handler=self.browser_navigate,
            ),
        }

    def browser_navigate(self, arguments: dict[str, Any]) -> McpCallToolResult:
        url = required_string(arguments, "url")
        wait_until = optional_string(arguments, "wait_until")
        if wait_until is None:
            wait_until = "load"
        if wait_until not in _WAIT_UNTIL_VALUES:
            raise HTTPException(status_code=422, detail="wait_until must be a supported browser lifecycle value")
        timeout = optional_int_range(
            arguments,
            "timeout",
            default=30000,
            minimum=1000,
            maximum=120000,
        )
        return json_result(
            self.browser_sessions.navigate(
                url=url,
                wait_until=wait_until,
                timeout=timeout,
            )
        )

from __future__ import annotations

import base64
from typing import Any

from fastapi import HTTPException

from .. import config
from ..browser.manager import BrowserSessionManager
from ..schemas import McpCallToolResult, McpContentItem
from .models import McpTool
from .results import json_result
from .validators import optional_int_range, optional_string, required_string

_WAIT_UNTIL_VALUES = {"load", "domcontentloaded", "networkidle", "commit"}
_IMAGE_FORMATS = {"png", "jpg", "jpeg"}


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
            "browser_text": McpTool(
                name="browser_text",
                description="Read visible text from the active sandbox browser tab.",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                handler=self.browser_text,
            ),
            "browser_screenshot": McpTool(
                name="browser_screenshot",
                description="Capture the active sandbox browser tab as MCP image content.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "format": {"type": "string", "enum": ["png", "jpg", "jpeg"]},
                        "quality": {"type": "integer", "minimum": 0, "maximum": 100},
                    },
                    "required": [],
                },
                handler=self.browser_screenshot,
            ),
            "browser_evaluate": McpTool(
                name="browser_evaluate",
                description="Evaluate a JavaScript expression in the active sandbox browser tab.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "script": {"type": "string"},
                    },
                    "required": ["script"],
                },
                handler=self.browser_evaluate,
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

    def browser_text(self, _: dict[str, Any]) -> McpCallToolResult:
        return McpCallToolResult(
            content=[McpContentItem(type="text", text=self.browser_sessions.text())]
        )

    def browser_screenshot(self, arguments: dict[str, Any]) -> McpCallToolResult:
        image_format = optional_string(arguments, "format")
        if image_format is None:
            image_format = "png"
        if image_format not in _IMAGE_FORMATS:
            raise HTTPException(status_code=422, detail="format must be png, jpg, or jpeg")
        quality = optional_int_range(
            arguments,
            "quality",
            default=None,
            minimum=0,
            maximum=100,
        )
        image, _ = self.browser_sessions.screenshot(image_format=image_format, quality=quality)
        if len(image) > config.MAX_BROWSER_SCREENSHOT_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"browser screenshot exceeds MAX_BROWSER_SCREENSHOT_BYTES={config.MAX_BROWSER_SCREENSHOT_BYTES}",
            )
        mime_type = "image/jpeg" if image_format in {"jpg", "jpeg"} else "image/png"
        return McpCallToolResult(
            content=[
                McpContentItem(
                    type="image",
                    data=base64.b64encode(image).decode("ascii"),
                    mimeType=mime_type,
                )
            ]
        )

    def browser_evaluate(self, arguments: dict[str, Any]) -> McpCallToolResult:
        script = required_string(arguments, "script")
        return json_result(self.browser_sessions.evaluate(script))

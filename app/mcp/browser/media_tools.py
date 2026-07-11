from __future__ import annotations

import base64
from typing import Any

from fastapi import HTTPException

from ... import config
from ...browser.manager import BrowserSessionManager
from ...schemas import McpCallToolResult, McpContentItem
from ..models import McpTool
from ..validators import optional_int_range, optional_string

_IMAGE_FORMATS = {"png", "jpg", "jpeg"}


class BrowserMediaMcpTools:
    def __init__(self, *, browser_sessions: BrowserSessionManager) -> None:
        self.browser_sessions = browser_sessions

    def tools(self) -> dict[str, McpTool]:
        return {
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
        }

    def browser_screenshot(self, arguments: dict[str, Any]) -> McpCallToolResult:
        image_format = optional_string(arguments, "format")
        if image_format is None:
            image_format = "png"
        if image_format not in _IMAGE_FORMATS:
            raise HTTPException(status_code=422, detail="format must be png, jpg, or jpeg")
        quality = optional_int_range(arguments, "quality", default=None, minimum=0, maximum=100)
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

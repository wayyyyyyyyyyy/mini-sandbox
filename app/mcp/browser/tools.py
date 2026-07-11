from __future__ import annotations

from ...browser.manager import BrowserSessionManager
from ..models import McpTool
from .interaction_tools import BrowserInteractionMcpTools
from .media_tools import BrowserMediaMcpTools
from .page_tools import BrowserPageMcpTools


class BrowserMcpTools:
    def __init__(self, *, browser_sessions: BrowserSessionManager) -> None:
        self._groups = (
            BrowserPageMcpTools(browser_sessions=browser_sessions),
            BrowserMediaMcpTools(browser_sessions=browser_sessions),
            BrowserInteractionMcpTools(browser_sessions=browser_sessions),
        )

    def tools(self) -> dict[str, McpTool]:
        tools: dict[str, McpTool] = {}
        for group in self._groups:
            tools.update(group.tools())
        return tools

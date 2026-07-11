from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import HTTPException

from ..browser.manager import BrowserSessionManager
from ..jupyter_sessions import JupyterSessionManager
from ..schemas import McpCallToolResult, McpListToolsResult, McpToolInfo
from ..shell_sessions import ShellSessionManager
from .browser_tools import BrowserMcpTools
from .file_tools import FileMcpTools
from .jupyter_tools import JupyterMcpTools
from .models import McpTool
from .ports_tools import PortsMcpTools
from .shell_tools import ShellMcpTools

SANDBOX_SERVER_NAME = "sandbox"


class SandboxMcpTools:
    def __init__(
        self,
        *,
        shell_sessions: ShellSessionManager,
        jupyter_sessions: JupyterSessionManager,
        resolve_exec_dir: Callable[[str | None], Path],
        relative_path: Callable[[Path], str],
        browser_sessions: BrowserSessionManager,
    ) -> None:
        self._tools: dict[str, McpTool] = {}
        self._register(BrowserMcpTools(browser_sessions=browser_sessions).tools())
        self._register(FileMcpTools(relative_path=relative_path).tools())
        self._register(ShellMcpTools(shell_sessions=shell_sessions, resolve_exec_dir=resolve_exec_dir).tools())
        self._register(JupyterMcpTools(jupyter_sessions=jupyter_sessions, resolve_exec_dir=resolve_exec_dir).tools())
        self._register(PortsMcpTools().tools())

    def list_servers(self) -> list[str]:
        return [SANDBOX_SERVER_NAME]

    def list_tools(self, server_name: str) -> McpListToolsResult:
        self._ensure_server(server_name)
        return McpListToolsResult(
            tools=[
                McpToolInfo(
                    name=tool.name,
                    description=tool.description,
                    inputSchema=tool.input_schema,
                )
                for tool in self._tools.values()
            ]
        )

    def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> McpCallToolResult:
        self._ensure_server(server_name)
        tool = self._tools.get(tool_name)
        if tool is None:
            raise HTTPException(status_code=404, detail=f"mcp tool not found: {tool_name}")
        return tool.handler(arguments)

    def _register(self, tools: dict[str, McpTool]) -> None:
        self._tools.update(tools)

    def _ensure_server(self, server_name: str) -> None:
        if server_name != SANDBOX_SERVER_NAME:
            raise HTTPException(status_code=404, detail=f"mcp server not found: {server_name}")

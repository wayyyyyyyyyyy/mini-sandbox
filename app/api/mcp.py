from fastapi import Depends, FastAPI

from ..auth import require_api_key
from ..mcp_tools import SandboxMcpTools
from ..schemas import McpCallToolResult, McpListToolsResult


def register_mcp_routes(app: FastAPI, mcp_tools: SandboxMcpTools) -> None:
    @app.get("/mcp/servers", response_model=list[str])
    def mcp_list_servers(
        include_hidden: bool = False,
        _: None = Depends(require_api_key),
    ) -> list[str]:
        return mcp_tools.list_servers()

    @app.get("/mcp/{server_name}/tools", response_model=McpListToolsResult)
    def mcp_list_tools(server_name: str, _: None = Depends(require_api_key)) -> McpListToolsResult:
        return mcp_tools.list_tools(server_name)

    @app.post("/mcp/{server_name}/tools/{tool_name}", response_model=McpCallToolResult)
    def mcp_call_tool(
        server_name: str,
        tool_name: str,
        arguments: dict,
        _: None = Depends(require_api_key),
    ) -> McpCallToolResult:
        return mcp_tools.call_tool(server_name, tool_name, arguments)

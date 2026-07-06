from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException

from .schemas import McpCallToolResult, McpContentItem, McpListToolsResult, McpToolInfo
from .security import ensure_file_size_allowed, resolve_workspace_path
from .shell_sessions import ShellSessionManager, shell_result
from .jupyter_sessions import JupyterSessionManager

ToolHandler = Callable[[dict[str, Any]], McpCallToolResult]


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler


class SandboxMcpTools:
    def __init__(
        self,
        *,
        shell_sessions: ShellSessionManager,
        jupyter_sessions: JupyterSessionManager,
        resolve_exec_dir: Callable[[str | None], Path],
        relative_path: Callable[[Path], str],
    ) -> None:
        self.shell_sessions = shell_sessions
        self.jupyter_sessions = jupyter_sessions
        self.resolve_exec_dir = resolve_exec_dir
        self.relative_path = relative_path
        self._tools = {
            "file_read": McpTool(
                name="file_read",
                description="Read a UTF-8 text file from the sandbox workspace.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                },
                handler=self._file_read,
            ),
            "file_write": McpTool(
                name="file_write",
                description="Write UTF-8 text content to a sandbox workspace file.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
                handler=self._file_write,
            ),
            "shell_exec": McpTool(
                name="shell_exec",
                description="Execute a shell command inside the sandbox workspace.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {"type": "number"},
                        "session_id": {"type": "string"},
                        "exec_dir": {"type": "string"},
                    },
                    "required": ["command"],
                },
                handler=self._shell_exec,
            ),
            "jupyter_execute": McpTool(
                name="jupyter_execute",
                description="Execute Python code in a persistent Jupyter kernel session.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "timeout": {"type": "integer"},
                        "session_id": {"type": "string"},
                        "kernel_name": {"type": "string"},
                        "cwd": {"type": "string"},
                    },
                    "required": ["code"],
                },
                handler=self._jupyter_execute,
            ),
        }

    def list_servers(self) -> list[str]:
        return ["sandbox"]

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

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> McpCallToolResult:
        self._ensure_server(server_name)
        tool = self._tools.get(tool_name)
        if tool is None:
            raise HTTPException(status_code=404, detail=f"mcp tool not found: {tool_name}")
        return tool.handler(arguments)

    def _ensure_server(self, server_name: str) -> None:
        if server_name != "sandbox":
            raise HTTPException(status_code=404, detail=f"mcp server not found: {server_name}")

    def _file_read(self, arguments: dict[str, Any]) -> McpCallToolResult:
        path_text = _required_string(arguments, "path")
        path = resolve_workspace_path(path_text)
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail=f"file not found: {path_text}")
        content_bytes = path.read_bytes()
        ensure_file_size_allowed(content_bytes)
        return McpCallToolResult(content=[McpContentItem(type="text", text=content_bytes.decode("utf-8"))])

    def _file_write(self, arguments: dict[str, Any]) -> McpCallToolResult:
        path_text = _required_string(arguments, "path")
        content = _required_string(arguments, "content")
        path = resolve_workspace_path(path_text)
        content_bytes = content.encode("utf-8")
        ensure_file_size_allowed(content_bytes)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content_bytes)
        return _json_result({
            "path": self.relative_path(path),
            "bytes": path.stat().st_size,
        })

    def _shell_exec(self, arguments: dict[str, Any]) -> McpCallToolResult:
        command = _required_string(arguments, "command")
        exec_dir = self.resolve_exec_dir(arguments.get("exec_dir") or ".")
        session = self.shell_sessions.exec(
            command=command,
            session_id=_optional_string(arguments, "session_id"),
            exec_dir=exec_dir,
            async_mode=False,
            timeout=_optional_float(arguments, "timeout"),
            hard_timeout=None,
        )
        result = shell_result(session)
        return McpCallToolResult(
            content=[McpContentItem(type="json", data=result)],
            isError=result["status"] in {"killed", "closed"} or (result["exit_code"] not in {None, 0}),
        )

    def _jupyter_execute(self, arguments: dict[str, Any]) -> McpCallToolResult:
        code = _required_string(arguments, "code")
        cwd = self.resolve_exec_dir(arguments.get("cwd") or ".") if arguments.get("cwd") is not None else None
        result = self.jupyter_sessions.execute(
            code=code,
            timeout=_optional_int(arguments, "timeout") or 30,
            session_id=_optional_string(arguments, "session_id"),
            kernel_name=_optional_string(arguments, "kernel_name"),
            cwd=cwd,
        )
        return McpCallToolResult(
            content=[McpContentItem(type="json", data=result)],
            isError=result["status"] != "ok",
        )


def _json_result(data: dict[str, Any]) -> McpCallToolResult:
    return McpCallToolResult(content=[McpContentItem(type="json", data=data)])


def _required_string(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=422, detail=f"{key} must be a non-empty string")
    return value


def _optional_string(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"{key} must be a string")
    return value


def _optional_float(arguments: dict[str, Any], key: str) -> float | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, int | float) or value <= 0:
        raise HTTPException(status_code=422, detail=f"{key} must be a positive number")
    return float(value)


def _optional_int(arguments: dict[str, Any], key: str) -> int | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or value <= 0:
        raise HTTPException(status_code=422, detail=f"{key} must be a positive integer")
    return value

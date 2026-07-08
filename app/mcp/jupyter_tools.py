from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..jupyter_sessions import JupyterSessionManager
from ..schemas import McpCallToolResult, McpContentItem
from .models import McpTool
from .validators import optional_int, optional_string, required_string


class JupyterMcpTools:
    def __init__(
        self,
        *,
        jupyter_sessions: JupyterSessionManager,
        resolve_exec_dir: Callable[[str | None], Path],
    ) -> None:
        self.jupyter_sessions = jupyter_sessions
        self.resolve_exec_dir = resolve_exec_dir

    def tools(self) -> dict[str, McpTool]:
        return {
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
                handler=self.jupyter_execute,
            ),
        }

    def jupyter_execute(self, arguments: dict[str, Any]) -> McpCallToolResult:
        code = required_string(arguments, "code")
        cwd = self.resolve_exec_dir(arguments.get("cwd") or ".") if arguments.get("cwd") is not None else None
        result = self.jupyter_sessions.execute(
            code=code,
            timeout=optional_int(arguments, "timeout") or 30,
            session_id=optional_string(arguments, "session_id"),
            kernel_name=optional_string(arguments, "kernel_name"),
            cwd=cwd,
        )
        return McpCallToolResult(
            content=[McpContentItem(type="json", data=result)],
            isError=result["status"] != "ok",
        )

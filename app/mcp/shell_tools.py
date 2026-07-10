from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..schemas import McpCallToolResult, McpContentItem
from ..shell_sessions import ShellSessionManager, shell_result
from .models import McpTool
from .validators import optional_float, optional_string, required_string


class ShellMcpTools:
    def __init__(
        self,
        *,
        shell_sessions: ShellSessionManager,
        resolve_exec_dir: Callable[[str | None], Path],
    ) -> None:
        self.shell_sessions = shell_sessions
        self.resolve_exec_dir = resolve_exec_dir

    def tools(self) -> dict[str, McpTool]:
        return {
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
                handler=self.shell_exec,
            ),
        }

    def shell_exec(self, arguments: dict[str, Any]) -> McpCallToolResult:
        command = required_string(arguments, "command")
        exec_dir = self.resolve_exec_dir(arguments.get("exec_dir") or ".")
        session = self.shell_sessions.exec(
            command=command,
            session_id=optional_string(arguments, "session_id"),
            exec_dir=exec_dir,
            async_mode=False,
            timeout=optional_float(arguments, "timeout"),
            hard_timeout=None,
        )
        result = shell_result(session)
        return McpCallToolResult(
            content=[McpContentItem(type="json", data=result)],
            isError=result["status"] in {"killed", "closed"} or (result["exit_code"] not in {None, 0}),
        )

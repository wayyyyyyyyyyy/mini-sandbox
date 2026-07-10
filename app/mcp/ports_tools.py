from __future__ import annotations

from typing import Any

from ..api.ports import discover_listening_ports
from ..schemas import McpCallToolResult
from .models import McpTool
from .results import json_result


class PortsMcpTools:
    def tools(self) -> dict[str, McpTool]:
        return {
            "ports_list": McpTool(
                name="ports_list",
                description="List local TCP ports currently listening inside the sandbox.",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                handler=self.ports_list,
            ),
        }

    def ports_list(self, arguments: dict[str, Any]) -> McpCallToolResult:
        ports = [port.model_dump() for port in discover_listening_ports()]
        return json_result({"ports": ports})

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException

from ..schemas import McpCallToolResult, McpContentItem
from ..security import ensure_file_size_allowed, resolve_workspace_path
from ..files.search import grep_files, search_file
from .models import McpTool
from .results import json_result
from .validators import optional_bool, optional_int, optional_string_list, required_string


class FileMcpTools:
    def __init__(self, *, relative_path: Callable[[Path], str]) -> None:
        self.relative_path = relative_path

    def tools(self) -> dict[str, McpTool]:
        return {
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
                handler=self.file_read,
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
                handler=self.file_write,
            ),
            "file_search": McpTool(
                name="file_search",
                description="Search a UTF-8 text file with a regular expression.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "regex": {"type": "string"},
                        "case_insensitive": {"type": "boolean"},
                        "max_results": {"type": "integer"},
                    },
                    "required": ["path", "regex"],
                },
                handler=self.file_search,
            ),
            "file_grep": McpTool(
                name="file_grep",
                description="Search UTF-8 files under a sandbox workspace directory with a regular expression.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "pattern": {"type": "string"},
                        "include": {"type": "array", "items": {"type": "string"}},
                        "exclude": {"type": "array", "items": {"type": "string"}},
                        "case_insensitive": {"type": "boolean"},
                        "max_results": {"type": "integer"},
                    },
                    "required": ["path", "pattern"],
                },
                handler=self.file_grep,
            ),
        }

    def file_read(self, arguments: dict[str, Any]) -> McpCallToolResult:
        path_text = required_string(arguments, "path")
        path = resolve_workspace_path(path_text)
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail=f"file not found: {path_text}")
        content_bytes = path.read_bytes()
        ensure_file_size_allowed(content_bytes)
        return McpCallToolResult(content=[McpContentItem(type="text", text=content_bytes.decode("utf-8"))])

    def file_write(self, arguments: dict[str, Any]) -> McpCallToolResult:
        path_text = required_string(arguments, "path")
        content = required_string(arguments, "content")
        path = resolve_workspace_path(path_text)
        content_bytes = content.encode("utf-8")
        ensure_file_size_allowed(content_bytes)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content_bytes)
        return json_result({
            "path": self.relative_path(path),
            "bytes": path.stat().st_size,
        })

    def file_search(self, arguments: dict[str, Any]) -> McpCallToolResult:
        path_text = required_string(arguments, "path")
        regex = required_string(arguments, "regex")
        path = resolve_workspace_path(path_text)
        return json_result(search_file(
            path=path,
            regex=regex,
            case_insensitive=optional_bool(arguments, "case_insensitive") or False,
            max_results=optional_int(arguments, "max_results") or 100,
        ))

    def file_grep(self, arguments: dict[str, Any]) -> McpCallToolResult:
        path_text = required_string(arguments, "path")
        pattern = required_string(arguments, "pattern")
        path = resolve_workspace_path(path_text)
        return json_result(grep_files(
            path=path,
            pattern_text=pattern,
            include=optional_string_list(arguments, "include") or [],
            exclude=optional_string_list(arguments, "exclude") or [],
            case_insensitive=optional_bool(arguments, "case_insensitive") or False,
            max_results=optional_int(arguments, "max_results") or 100,
        ))

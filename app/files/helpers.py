import base64
import fnmatch
from pathlib import Path

from fastapi import HTTPException

from ..schemas import FileWriteRequest


def size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return 0


def file_content_bytes(request: FileWriteRequest) -> bytes:
    content = request.content
    if request.leading_newline:
        content = "\n" + content
    if request.trailing_newline:
        content = content + "\n"

    if request.encoding == "utf-8":
        return content.encode("utf-8")
    if request.encoding == "base64":
        try:
            return base64.b64decode(content, validate=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid base64 content") from exc
    if request.encoding == "raw":
        return content.encode("latin-1")
    raise HTTPException(status_code=400, detail=f"unsupported encoding: {request.encoding}")


def is_hidden_relative(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)

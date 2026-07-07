from fastapi import Depends, FastAPI, HTTPException

from ..auth import require_api_key
from ..core.paths import relative_path as _relative
from ..schemas import (
    FileInfo,
    FileListRequest,
    FileListResult,
    FileReadRequest,
    FileReadResult,
    FileReplaceRequest,
    FileReplaceResult,
    FileWriteRequest,
    FileWriteResult,
)
from ..security import ensure_file_size_allowed, resolve_workspace_path
from .helpers import file_content_bytes, is_hidden_relative, size


def register_file_content_routes(app: FastAPI) -> None:
    @app.post("/file/read", response_model=FileReadResult)
    def file_read(request: FileReadRequest, _: None = Depends(require_api_key)) -> FileReadResult:
        path = resolve_workspace_path(request.path)
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail=f"file not found: {request.path}")

        content_bytes = path.read_bytes()
        ensure_file_size_allowed(content_bytes)
        content = content_bytes.decode("utf-8")
        line_count = None
        if request.start_line is not None or request.end_line is not None:
            content = content.replace("\r\n", "\n")
            lines = content.splitlines(keepends=True)
            start = request.start_line or 0
            end = request.end_line if request.end_line is not None else len(lines)
            if end < start:
                raise HTTPException(status_code=400, detail="end_line must be greater than or equal to start_line")
            selected = lines[start:end]
            content = "".join(selected)
            line_count = len(selected)

        return FileReadResult(
            path=_relative(path),
            content=content,
            bytes=len(content.encode("utf-8")),
            line_count=line_count,
        )

    @app.post("/file/write", response_model=FileWriteResult)
    def file_write(request: FileWriteRequest, _: None = Depends(require_api_key)) -> FileWriteResult:
        path = resolve_workspace_path(request.path)
        content_bytes = file_content_bytes(request)
        ensure_file_size_allowed(content_bytes)

        if request.create_parent:
            path.parent.mkdir(parents=True, exist_ok=True)
        elif not path.parent.exists():
            raise HTTPException(status_code=400, detail=f"parent does not exist: {path.parent}")

        if request.append:
            with path.open("ab") as file:
                file.write(content_bytes)
        else:
            path.write_bytes(content_bytes)
        return FileWriteResult(path=_relative(path), bytes=path.stat().st_size)

    @app.post("/file/replace", response_model=FileReplaceResult)
    def file_replace(request: FileReplaceRequest, _: None = Depends(require_api_key)) -> FileReplaceResult:
        path = resolve_workspace_path(request.path)
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail=f"file not found: {request.path}")

        content = path.read_text(encoding="utf-8")
        if request.old_str not in content:
            raise HTTPException(status_code=404, detail=f"old_str not found in file: {request.path}")

        match_count = content.count(request.old_str)
        if request.count is not None:
            replacement_limit = request.count
        elif request.all:
            replacement_limit = match_count
        else:
            replacement_limit = 1

        replaced = min(match_count, replacement_limit)
        updated = content.replace(request.old_str, request.new_str, replacement_limit)
        ensure_file_size_allowed(updated.encode("utf-8"))
        path.write_bytes(updated.encode("utf-8"))

        return FileReplaceResult(path=_relative(path), replaced=replaced, changed=replaced > 0)

    @app.post("/file/list", response_model=FileListResult)
    def file_list(request: FileListRequest, _: None = Depends(require_api_key)) -> FileListResult:
        path = resolve_workspace_path(request.path)
        if not path.exists() or not path.is_dir():
            raise HTTPException(status_code=404, detail=f"directory not found: {request.path}")

        entries = []
        children = path.rglob("*") if request.recursive else path.iterdir()
        for child in sorted(children, key=lambda item: str(item.relative_to(path))):
            if not request.show_hidden and is_hidden_relative(child.relative_to(path)):
                continue
            if child.is_file():
                kind = "file"
            elif child.is_dir():
                kind = "directory"
            else:
                kind = "other"
            entries.append(
                FileInfo(
                    path=_relative(child),
                    kind=kind,
                    bytes=size(child) if request.include_size else 0,
                )
            )

        return FileListResult(path=_relative(path), entries=entries)

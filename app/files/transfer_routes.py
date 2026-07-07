from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..auth import require_api_key
from ..core.paths import relative_path as _relative
from ..schemas import FileWriteResult
from ..security import ensure_file_size_allowed, resolve_workspace_path


def register_file_transfer_routes(app: FastAPI) -> None:
    @app.post("/file/upload", response_model=FileWriteResult)
    async def file_upload(
        path: str = Form(...),
        file: UploadFile = File(...),
        _: None = Depends(require_api_key),
    ) -> FileWriteResult:
        target = resolve_workspace_path(path)
        content = await file.read()
        ensure_file_size_allowed(content)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return FileWriteResult(path=_relative(target), bytes=target.stat().st_size)

    @app.get("/file/download")
    def file_download(path: str, _: None = Depends(require_api_key)) -> FileResponse:
        target = resolve_workspace_path(path)
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail=f"file not found: {path}")
        return FileResponse(path=target, filename=target.name, media_type="application/octet-stream")

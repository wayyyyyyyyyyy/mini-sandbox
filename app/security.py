from pathlib import Path

from fastapi import HTTPException

from .config import MAX_FILE_BYTES, WORKSPACE


def ensure_workspace() -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)


def resolve_workspace_path(path: str) -> Path:
    if not path:
        raise HTTPException(status_code=400, detail="path is required")

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = WORKSPACE / candidate

    resolved = candidate.expanduser().resolve()
    try:
        resolved.relative_to(WORKSPACE)
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail=f"path escapes workspace: {path}",
        ) from exc

    return resolved


def ensure_file_size_allowed(content: bytes) -> None:
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file exceeds MAX_FILE_BYTES={MAX_FILE_BYTES}",
        )


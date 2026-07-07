from pathlib import Path

from fastapi import HTTPException

from . import security


def relative_path(path: Path) -> str:
    return path.resolve().relative_to(security.WORKSPACE).as_posix()


def resolve_exec_dir(path: str) -> Path:
    exec_dir = security.resolve_workspace_path(path)
    if not exec_dir.exists() or not exec_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"exec_dir is not a directory: {exec_dir}")
    return exec_dir

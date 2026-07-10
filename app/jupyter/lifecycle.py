from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import HTTPException
from jupyter_client import KernelManager

from .config import JUPYTER_KERNEL_READY_TIMEOUT_SECONDS
from .environment import prepare_jupyter_environment
from .models import JupyterSession


def start_session(
    *,
    session_id: str,
    kernel_name: str,
    cwd: Path,
    now: datetime,
) -> JupyterSession:
    prepare_jupyter_environment()
    manager = KernelManager(kernel_name=kernel_name)
    try:
        manager.start_kernel(cwd=str(cwd))
        client = manager.blocking_client()
        client.start_channels()
        client.wait_for_ready(timeout=JUPYTER_KERNEL_READY_TIMEOUT_SECONDS)
    except Exception as exc:
        try:
            manager.shutdown_kernel(now=True)
        except Exception:
            pass
        raise HTTPException(status_code=503, detail=f"failed to start jupyter kernel: {exc}") from exc
    return JupyterSession(
        session_id=session_id,
        kernel_name=kernel_name,
        cwd=cwd,
        manager=manager,
        client=client,
        created_at=now,
        last_used_at=now,
    )


def shutdown_session(session: JupyterSession) -> None:
    try:
        session.client.stop_channels()
    finally:
        try:
            session.manager.shutdown_kernel(now=True)
        except Exception:
            pass

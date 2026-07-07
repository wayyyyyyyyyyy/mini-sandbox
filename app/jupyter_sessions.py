from __future__ import annotations

import time
import uuid
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty
from threading import Lock
from typing import Any

from fastapi import HTTPException
from jupyter_client import KernelManager
from jupyter_client.kernelspec import KernelSpecManager

from . import security

MAX_JUPYTER_SESSIONS = 16
JUPYTER_SESSION_TIMEOUT_SECONDS = 1800
JUPYTER_KERNEL_READY_TIMEOUT_SECONDS = float(os.getenv("JUPYTER_KERNEL_READY_TIMEOUT_SECONDS", "30"))


@dataclass
class JupyterSession:
    session_id: str
    kernel_name: str
    cwd: Path
    manager: KernelManager
    client: Any
    created_at: datetime
    last_used_at: datetime
    lock: Lock = field(default_factory=Lock)


class JupyterSessionManager:
    def __init__(
        self,
        *,
        max_sessions: int = MAX_JUPYTER_SESSIONS,
        session_timeout_seconds: int = JUPYTER_SESSION_TIMEOUT_SECONDS,
    ) -> None:
        self.max_sessions = max_sessions
        self.session_timeout_seconds = session_timeout_seconds
        self._sessions: dict[str, JupyterSession] = {}
        self._lock = Lock()
        self._kernel_spec_manager = KernelSpecManager()

    def info(self) -> dict[str, Any]:
        _prepare_jupyter_environment()
        return {
            "default_kernel": self.default_kernel(),
            "available_kernels": self.available_kernels(),
            "active_sessions": len(self.list()),
            "session_timeout_seconds": self.session_timeout_seconds,
            "max_sessions": self.max_sessions,
            "description": "Jupyter kernel execution service",
            "kernel_detection": "jupyter_client KernelSpecManager",
        }

    def default_kernel(self) -> str:
        kernels = self.available_kernels()
        if "python3" in kernels:
            return "python3"
        if not kernels:
            raise HTTPException(status_code=503, detail="no Jupyter kernels are available")
        return kernels[0]

    def available_kernels(self) -> list[str]:
        _prepare_jupyter_environment()
        return sorted(self._kernel_spec_manager.find_kernel_specs())

    def create_session(
        self,
        *,
        session_id: str | None = None,
        kernel_name: str | None = None,
        cwd: Path | None = None,
    ) -> JupyterSession:
        session_id = session_id or f"jp_{uuid.uuid4().hex}"
        kernel_name = kernel_name or self.default_kernel()
        cwd = cwd or security.WORKSPACE
        now = _utcnow()
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing
            if self.max_sessions > 0 and len(self._sessions) >= self.max_sessions:
                raise HTTPException(status_code=429, detail="jupyter session limit exceeded")
            session = self._start_session(
                session_id=session_id,
                kernel_name=kernel_name,
                cwd=cwd,
                now=now,
            )
            self._sessions[session_id] = session
            return session

    def list(self) -> dict[str, JupyterSession]:
        with self._lock:
            return dict(self._sessions)

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            raise HTTPException(status_code=404, detail=f"jupyter session not found: {session_id}")
        self._shutdown_session(session)

    def delete_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            self._shutdown_session(session)

    def execute(
        self,
        *,
        code: str,
        timeout: int,
        session_id: str | None = None,
        kernel_name: str | None = None,
        cwd: Path | None = None,
    ) -> dict[str, Any]:
        session = self.create_session(session_id=session_id, kernel_name=kernel_name, cwd=cwd)
        with session.lock:
            session.last_used_at = _utcnow()
            msg_id = session.client.execute(code, allow_stdin=False, stop_on_error=True)
            return self._collect_execute_result(session, code=code, msg_id=msg_id, timeout=timeout)

    def session_info(self, session: JupyterSession) -> dict[str, Any]:
        now = _utcnow()
        return {
            "kernel_name": session.kernel_name,
            "last_used": session.last_used_at.isoformat(),
            "age_seconds": int((now - session.created_at).total_seconds()),
        }

    def _start_session(
        self,
        *,
        session_id: str,
        kernel_name: str,
        cwd: Path,
        now: datetime,
    ) -> JupyterSession:
        if kernel_name not in self.available_kernels():
            raise HTTPException(status_code=404, detail=f"jupyter kernel not found: {kernel_name}")
        _prepare_jupyter_environment()
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

    def _collect_execute_result(
        self,
        session: JupyterSession,
        *,
        code: str,
        msg_id: str,
        timeout: int,
    ) -> dict[str, Any]:
        outputs = []
        execution_count = None
        status = "ok"
        deadline = time.monotonic() + timeout

        while True:
            remaining = max(deadline - time.monotonic(), 0)
            if remaining == 0:
                return self._timeout_result(session, code=code, msg_id=msg_id, outputs=outputs)
            try:
                message = session.client.get_iopub_msg(timeout=remaining)
            except Empty:
                return self._timeout_result(session, code=code, msg_id=msg_id, outputs=outputs)
            if message.get("parent_header", {}).get("msg_id") != msg_id:
                continue

            msg_type = message["header"]["msg_type"]
            content = message["content"]
            if msg_type == "status" and content.get("execution_state") == "idle":
                break
            if msg_type == "stream":
                outputs.append({
                    "output_type": "stream",
                    "name": content.get("name"),
                    "text": content.get("text"),
                })
            elif msg_type in {"execute_result", "display_data"}:
                execution_count = content.get("execution_count", execution_count)
                outputs.append({
                    "output_type": msg_type,
                    "data": content.get("data"),
                    "metadata": content.get("metadata"),
                    "execution_count": content.get("execution_count"),
                })
            elif msg_type == "error":
                status = "error"
                outputs.append({
                    "output_type": "error",
                    "ename": content.get("ename"),
                    "evalue": content.get("evalue"),
                    "traceback": content.get("traceback"),
                })
            elif msg_type == "execute_input":
                execution_count = content.get("execution_count", execution_count)

        reply = self._shell_reply(session, msg_id=msg_id, timeout=1)
        if reply is not None:
            content = reply.get("content", {})
            status = content.get("status", status)
            execution_count = content.get("execution_count", execution_count)

        session.last_used_at = _utcnow()
        return {
            "kernel_name": session.kernel_name,
            "session_id": session.session_id,
            "status": status,
            "execution_count": execution_count,
            "outputs": outputs,
            "code": code,
            "msg_id": msg_id,
        }

    def _shell_reply(self, session: JupyterSession, *, msg_id: str, timeout: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                reply = session.client.get_shell_msg(timeout=max(deadline - time.monotonic(), 0))
            except Empty:
                return None
            if reply.get("parent_header", {}).get("msg_id") == msg_id:
                return reply
        return None

    def _timeout_result(
        self,
        session: JupyterSession,
        *,
        code: str,
        msg_id: str,
        outputs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            session.manager.interrupt_kernel()
        except Exception:
            pass
        return {
            "kernel_name": session.kernel_name,
            "session_id": session.session_id,
            "status": "timeout",
            "execution_count": None,
            "outputs": outputs,
            "code": code,
            "msg_id": msg_id,
        }

    def _shutdown_session(self, session: JupyterSession) -> None:
        try:
            session.client.stop_channels()
        finally:
            try:
                session.manager.shutdown_kernel(now=True)
            except Exception:
                pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _prepare_jupyter_environment() -> None:
    root = security.WORKSPACE
    paths = {
        "IPYTHONDIR": root / ".ipython",
        "JUPYTER_CONFIG_DIR": root / ".jupyter" / "config",
        "JUPYTER_DATA_DIR": root / ".jupyter" / "data",
        "JUPYTER_RUNTIME_DIR": root / ".jupyter" / "runtime",
    }
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path)

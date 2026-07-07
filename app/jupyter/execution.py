from __future__ import annotations

import time
from queue import Empty
from typing import Any

from .models import JupyterSession, utcnow


def execute_in_session(session: JupyterSession, *, code: str, timeout: int) -> dict[str, Any]:
    with session.lock:
        session.last_used_at = utcnow()
        msg_id = session.client.execute(code, allow_stdin=False, stop_on_error=True)
        return collect_execute_result(session, code=code, msg_id=msg_id, timeout=timeout)


def collect_execute_result(
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
            return timeout_result(session, code=code, msg_id=msg_id, outputs=outputs)
        try:
            message = session.client.get_iopub_msg(timeout=remaining)
        except Empty:
            return timeout_result(session, code=code, msg_id=msg_id, outputs=outputs)
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

    reply = shell_reply(session, msg_id=msg_id, timeout=1)
    if reply is not None:
        content = reply.get("content", {})
        status = content.get("status", status)
        execution_count = content.get("execution_count", execution_count)

    session.last_used_at = utcnow()
    return {
        "kernel_name": session.kernel_name,
        "session_id": session.session_id,
        "status": status,
        "execution_count": execution_count,
        "outputs": outputs,
        "code": code,
        "msg_id": msg_id,
    }


def shell_reply(session: JupyterSession, *, msg_id: str, timeout: float) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            reply = session.client.get_shell_msg(timeout=max(deadline - time.monotonic(), 0))
        except Empty:
            return None
        if reply.get("parent_header", {}).get("msg_id") == msg_id:
            return reply
    return None


def timeout_result(
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

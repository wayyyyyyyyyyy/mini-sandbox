import asyncio
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from ...auth import require_http_credentials
from ...shell_sessions import ShellSessionManager
from .utils import optional_int


def register_shell_websocket_routes(
    app: FastAPI,
    shell_sessions: ShellSessionManager,
    resolve_exec_dir: Callable[[str], Path],
) -> None:
    @app.websocket("/shell/ws")
    async def shell_websocket(websocket: WebSocket):
        try:
            require_http_credentials(
                x_sandbox_api_key=websocket.headers.get("x-sandbox-api-key"),
                authorization=websocket.headers.get("authorization"),
                ticket=websocket.query_params.get("ticket"),
            )
        except HTTPException:
            await websocket.close(code=1008)
            return

        exec_dir = resolve_exec_dir(websocket.query_params.get("exec_dir") or ".")
        session = shell_sessions.start_interactive(
            session_id=websocket.query_params.get("session_id"),
            exec_dir=exec_dir,
            cols=optional_int(websocket.query_params.get("cols")),
            rows=optional_int(websocket.query_params.get("rows")),
        )
        await websocket.accept()
        await websocket.send_json({"type": "session", "session_id": session.session_id})

        stop_event = asyncio.Event()
        sender = asyncio.create_task(
            shell_ws_output_pump(websocket, shell_sessions, session.session_id, stop_event)
        )

        try:
            while True:
                message = await websocket.receive_json()
                message_type = message.get("type")
                if message_type == "input":
                    shell_sessions.write_raw(session_id=session.session_id, data=str(message.get("data", "")))
                elif message_type == "resize":
                    data = message.get("data") if isinstance(message.get("data"), dict) else {}
                    cols = optional_int(data.get("cols"))
                    rows = optional_int(data.get("rows"))
                    if cols is not None and rows is not None:
                        shell_sessions.resize(session_id=session.session_id, cols=cols, rows=rows)
                elif message_type == "pong":
                    continue
                elif message_type == "ping":
                    await websocket.send_json({"type": "pong", "data": message.get("data")})
        except WebSocketDisconnect:
            pass
        finally:
            stop_event.set()
            sender.cancel()
            shell_sessions.close(session.session_id)


async def shell_ws_output_pump(
    websocket: WebSocket,
    shell_sessions: ShellSessionManager,
    session_id: str,
    stop_event: asyncio.Event,
) -> None:
    offset = 0
    while not stop_event.is_set():
        output, offset, _ = await asyncio.to_thread(shell_sessions.wait_for_output, session_id, offset, 0.2)
        if output:
            await websocket.send_json({"type": "output", "data": output})
        await asyncio.sleep(0)

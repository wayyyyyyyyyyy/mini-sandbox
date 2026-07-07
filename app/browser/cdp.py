from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException
from websockets.sync.client import connect


@dataclass
class _PendingCdpCall:
    event: threading.Event
    result: dict[str, Any] | None = None
    error: str | None = None


class CdpClient:
    def __init__(self, websocket_url: str) -> None:
        self.websocket_url = websocket_url
        self._socket = connect(websocket_url, open_timeout=5)
        self._next_id = 1
        self._send_lock = threading.Lock()
        self._pending: dict[int, _PendingCdpCall] = {}
        self._pending_lock = threading.Lock()
        self._event_handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._event_lock = threading.Lock()
        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def call(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 5) -> dict[str, Any]:
        pending = _PendingCdpCall(event=threading.Event())
        message_id = self._send(method, params, pending=pending)
        if not pending.event.wait(timeout):
            with self._pending_lock:
                self._pending.pop(message_id, None)
            raise HTTPException(status_code=408, detail=f"CDP method timed out: {method}")
        if pending.error is not None:
            raise HTTPException(status_code=400, detail=pending.error)
        return pending.result or {}

    def send(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send(method, params, pending=None)

    def add_event_handler(self, method: str, handler: Callable[[dict[str, Any]], None]) -> None:
        with self._event_lock:
            self._event_handlers.setdefault(method, []).append(handler)

    def close(self) -> None:
        self._closed = True
        try:
            self._socket.close()
        except Exception:
            pass
        self._fail_pending("CDP client closed")

    def _send(
        self,
        method: str,
        params: dict[str, Any] | None,
        *,
        pending: _PendingCdpCall | None,
    ) -> int:
        if self._closed:
            raise HTTPException(status_code=400, detail="CDP client closed")
        with self._send_lock:
            message_id = self._next_id
            self._next_id += 1
            if pending is not None:
                with self._pending_lock:
                    self._pending[message_id] = pending
            self._socket.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
            return message_id

    def _read_loop(self) -> None:
        while not self._closed:
            try:
                raw = self._socket.recv(timeout=0.2)
            except TimeoutError:
                continue
            except Exception as exc:
                if not self._closed:
                    self._fail_pending(str(exc))
                return
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            message_id = message.get("id")
            if message_id is not None:
                with self._pending_lock:
                    pending = self._pending.pop(message_id, None)
                if pending is None:
                    continue
                if "error" in message:
                    pending.error = message["error"].get("message", "CDP error")
                else:
                    pending.result = message.get("result", {})
                pending.event.set()
                continue
            method = message.get("method")
            if method is None:
                continue
            params = message.get("params", {})
            with self._event_lock:
                handlers = list(self._event_handlers.get(method, []))
            for handler in handlers:
                try:
                    handler(params)
                except Exception:
                    pass

    def _fail_pending(self, error: str) -> None:
        with self._pending_lock:
            pending_calls = list(self._pending.values())
            self._pending.clear()
        for pending in pending_calls:
            pending.error = error
            pending.event.set()

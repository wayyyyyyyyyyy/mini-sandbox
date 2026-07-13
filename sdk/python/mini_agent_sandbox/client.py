from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from .browser import BrowserClient
from .errors import SandboxAPIError
from .sse import iter_sse_events, parse_sse


class SandboxClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        http_client: Any | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = http_client or httpx.Client(base_url=self.base_url)
        self._owns_client = http_client is None
        self.file = FileClient(self)
        self.browser = BrowserClient(self)
        self.bash = BashClient(self)
        self.shell = ShellClient(self)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> SandboxClient:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def context(self) -> dict[str, Any]:
        return self._request("GET", "/context")

    def ticket(self) -> dict[str, Any]:
        return self._request("POST", "/tickets")

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        response = self._raw_request(method, path, json=json, params=params, timeout=timeout)
        return self._unwrap(response)

    def _raw_request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        request_headers = self._headers()
        if headers:
            request_headers.update(headers)
        request_kwargs: dict[str, Any] = {
            "json": json,
            "params": params,
            "headers": request_headers,
        }
        if timeout is not None:
            request_kwargs["timeout"] = timeout
        return self._client.request(
            method,
            self._url(path),
            **request_kwargs,
        )

    def _raw_stream(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        request_headers = self._headers()
        if headers:
            request_headers.update(headers)
        request_kwargs: dict[str, Any] = {
            "json": json,
            "params": params,
            "headers": request_headers,
        }
        if timeout is not None:
            request_kwargs["timeout"] = timeout
        return self._client.stream(
            method,
            self._url(path),
            **request_kwargs,
        )

    def _request_bytes(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> bytes:
        response = self._raw_request(method, path, params=params)
        self._raise_for_error(response)
        return response.content

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}{path}" if self.base_url else path

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"X-Sandbox-Api-Key": self.api_key}

    def _unwrap(self, response: Any) -> Any:
        body = _json_body(response)
        self._raise_for_error(response, body)
        if isinstance(body, dict) and "success" in body:
            return body.get("data")
        return body

    def _raise_for_error(self, response: Any, body: Any | None = None) -> None:
        body = _json_body(response) if body is None else body
        status_code = getattr(response, "status_code", 0)
        if isinstance(body, dict) and "success" in body:
            if status_code >= 400 or body.get("success") is not True:
                raise SandboxAPIError(
                    status_code=status_code,
                    message=str(body.get("message") or "Sandbox API error"),
                    data=body.get("data"),
                )
            return
        if status_code >= 400:
            raise SandboxAPIError(status_code=status_code, message="Sandbox API error", data=body)


class FileClient:
    def __init__(self, client: SandboxClient):
        self._client = client

    def read(
        self,
        path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        payload = {"path": path}
        if start_line is not None:
            payload["start_line"] = start_line
        if end_line is not None:
            payload["end_line"] = end_line
        return self._client._request("POST", "/file/read", json=payload)

    def write(
        self,
        path: str,
        content: str,
        *,
        create_parent: bool = True,
        encoding: str = "utf-8",
        append: bool = False,
        leading_newline: bool = False,
        trailing_newline: bool = False,
    ) -> dict[str, Any]:
        return self._client._request(
            "POST",
            "/file/write",
            json={
                "path": path,
                "content": content,
                "create_parent": create_parent,
                "encoding": encoding,
                "append": append,
                "leading_newline": leading_newline,
                "trailing_newline": trailing_newline,
            },
        )

    def replace(
        self,
        path: str,
        old_str: str,
        new_str: str,
        *,
        all: bool = False,
        count: int | None = None,
    ) -> dict[str, Any]:
        payload = {"path": path, "old_str": old_str, "new_str": new_str, "all": all}
        if count is not None:
            payload["count"] = count
        return self._client._request("POST", "/file/replace", json=payload)

    def download(self, path: str) -> bytes:
        return self._client._request_bytes("GET", "/file/download", params={"path": path})

    def watch(
        self,
        path: str = ".",
        *,
        recursive: bool = True,
        exclude: list[str] | None = None,
        include_patterns: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._client._request(
            "POST",
            "/file/watch",
            json={
                "path": path,
                "recursive": recursive,
                "exclude": exclude or [],
                "include_patterns": include_patterns or [],
            },
        )

    def watch_poll(
        self,
        watcher_id: str,
        *,
        cursor: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        return self._client._request(
            "POST",
            f"/file/watch/{watcher_id}/poll",
            json={"cursor": cursor, "limit": limit},
        )

    def wait(
        self,
        path: str,
        *,
        timeout: float = 30,
        event_types: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._client._request(
            "POST",
            "/file/watch/wait",
            json={
                "path": path,
                "timeout": timeout,
                "event_types": event_types or ["create", "write", "remove", "rename", "chmod"],
            },
            timeout=_watch_transport_timeout(timeout),
        )

    def watch_events(
        self,
        watcher_id: str,
        *,
        timeout: float = 30,
        heartbeat_interval: float = 15,
        last_event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": timeout,
            "heartbeat_interval": heartbeat_interval,
        }
        _set_optional(params, "last_event_id", last_event_id)
        response = self._client._raw_request(
            "GET",
            f"/file/watch/{watcher_id}/events",
            params=params,
            headers={"Accept": "text/event-stream"},
            timeout=_watch_transport_timeout(timeout),
        )
        self._client._raise_for_error(response)
        return parse_sse(response.text)

    def watch_events_stream(
        self,
        watcher_id: str,
        *,
        timeout: float = 30,
        heartbeat_interval: float = 15,
        last_event_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": timeout,
            "heartbeat_interval": heartbeat_interval,
        }
        _set_optional(params, "last_event_id", last_event_id)
        with self._client._raw_stream(
            "GET",
            f"/file/watch/{watcher_id}/events",
            params=params,
            headers={"Accept": "text/event-stream"},
            timeout=_watch_transport_timeout(timeout),
        ) as response:
            if response.status_code >= 400:
                response.read()
                self._client._raise_for_error(response)
            yield from iter_sse_events(response.iter_lines())

    def watch_delete(self, watcher_id: str) -> dict[str, Any]:
        return self._client._request("DELETE", f"/file/watch/{watcher_id}")


class BashClient:
    def __init__(self, client: SandboxClient):
        self._client = client

    def exec(
        self,
        command: str,
        *,
        session_id: str | None = None,
        exec_dir: str | None = None,
        timeout: float | None = None,
        hard_timeout: float | None = None,
        async_mode: bool = False,
        max_output_length: int | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "command": command,
            "async_mode": async_mode,
            "env": env or {},
        }
        _set_optional(payload, "session_id", session_id)
        _set_optional(payload, "exec_dir", exec_dir)
        _set_optional(payload, "timeout", timeout)
        _set_optional(payload, "hard_timeout", hard_timeout)
        _set_optional(payload, "max_output_length", max_output_length)
        return self._client._request("POST", "/bash/exec", json=payload)

    def output(
        self,
        session_id: str,
        *,
        command_id: str | None = None,
        offset: int = 0,
        stderr_offset: int = 0,
        wait: bool = False,
        wait_timeout: float = 30,
    ) -> dict[str, Any]:
        return self._client._request(
            "POST",
            "/bash/output",
            json={
                "session_id": session_id,
                "command_id": command_id,
                "offset": offset,
                "stderr_offset": stderr_offset,
                "wait": wait,
                "wait_timeout": wait_timeout,
            },
        )


class ShellClient:
    def __init__(self, client: SandboxClient):
        self._client = client

    def exec(
        self,
        command: str,
        *,
        id: str | None = None,
        exec_dir: str | None = None,
        timeout: float | None = None,
        hard_timeout: float | None = None,
        async_mode: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"command": command, "async_mode": async_mode}
        _set_optional(payload, "id", id)
        _set_optional(payload, "exec_dir", exec_dir)
        _set_optional(payload, "timeout", timeout)
        _set_optional(payload, "hard_timeout", hard_timeout)
        return self._client._request("POST", "/shell/exec", json=payload)

    def view(self, id: str) -> dict[str, Any]:
        return self._client._request("POST", "/shell/view", json={"id": id})

    def write(self, id: str, input: str, *, press_enter: bool = True) -> dict[str, Any]:
        return self._client._request(
            "POST",
            "/shell/write",
            json={"id": id, "input": input, "press_enter": press_enter},
        )


def _json_body(response: Any) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _set_optional(payload: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        payload[key] = value


def _watch_transport_timeout(wait_timeout: float) -> float:
    # Leave room for request setup and the server to finish its final response.
    return wait_timeout + 5

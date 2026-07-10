import asyncio

import httpx
import websockets
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from ..auth import require_api_key, require_http_credentials


def register_proxy_routes(app: FastAPI) -> None:
    @app.api_route(
        "/proxy/{port}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        include_in_schema=False,
    )
    @app.api_route(
        "/proxy/{port}/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        include_in_schema=False,
    )
    async def proxy_http(
        port: int,
        request: Request,
        path: str = "",
        _: None = Depends(require_api_key),
    ) -> Response:
        _validate_proxy_port(port)

        target_url = f"http://127.0.0.1:{port}/{path}"
        if request.url.query:
            target_url = f"{target_url}?{request.url.query}"

        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                upstream = await client.request(
                    request.method,
                    target_url,
                    content=await request.body(),
                    headers=_proxy_request_headers(request.headers, port),
                )
        except httpx.ConnectError as exc:
            raise HTTPException(status_code=502, detail=f"proxy upstream unavailable: {port}") from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail=f"proxy upstream timed out: {port}") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"proxy upstream error: {exc}") from exc

        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=_proxy_response_headers(upstream.headers),
            media_type=upstream.headers.get("content-type"),
        )

    @app.websocket("/proxy/{port}")
    @app.websocket("/proxy/{port}/{path:path}")
    async def proxy_websocket(websocket: WebSocket, port: int, path: str = "") -> None:
        try:
            require_http_credentials(
                x_sandbox_api_key=websocket.headers.get("x-sandbox-api-key"),
                authorization=websocket.headers.get("authorization"),
                ticket=websocket.query_params.get("ticket"),
            )
            _validate_proxy_port(port)
        except HTTPException:
            await websocket.close(code=1008)
            return

        upstream_url = f"ws://127.0.0.1:{port}/{path}"
        if websocket.url.query:
            upstream_url = f"{upstream_url}?{websocket.url.query}"

        try:
            async with websockets.connect(
                upstream_url,
                additional_headers=_proxy_websocket_request_headers(websocket.headers, port),
                open_timeout=5,
            ) as upstream:
                await websocket.accept()
                await _proxy_websocket_messages(websocket, upstream)
        except (OSError, TimeoutError, websockets.InvalidHandshake):
            await websocket.close(code=1011)


def _validate_proxy_port(port: int) -> None:
    if port < 1 or port > 65535:
        raise HTTPException(status_code=422, detail="proxy port must be between 1 and 65535")


async def _proxy_websocket_messages(websocket: WebSocket, upstream) -> None:
    client_to_upstream = asyncio.create_task(_websocket_client_to_upstream(websocket, upstream))
    upstream_to_client = asyncio.create_task(_websocket_upstream_to_client(websocket, upstream))
    done, pending = await asyncio.wait(
        {client_to_upstream, upstream_to_client},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    for task in done:
        exception = task.exception()
        if exception is not None and not isinstance(exception, (WebSocketDisconnect, websockets.ConnectionClosed)):
            raise exception


async def _websocket_client_to_upstream(websocket: WebSocket, upstream) -> None:
    while True:
        message = await websocket.receive()
        message_type = message.get("type")
        if message_type == "websocket.disconnect":
            await upstream.close()
            return
        if "text" in message:
            await upstream.send(message["text"])
        elif "bytes" in message:
            await upstream.send(message["bytes"])


async def _websocket_upstream_to_client(websocket: WebSocket, upstream) -> None:
    async for message in upstream:
        if isinstance(message, bytes):
            await websocket.send_bytes(message)
        else:
            await websocket.send_text(message)


def _proxy_request_headers(headers, port: int) -> dict[str, str]:
    excluded = {
        "host",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
    forwarded = {
        key: value
        for key, value in headers.items()
        if key.lower() not in excluded
    }
    forwarded["host"] = f"127.0.0.1:{port}"
    return forwarded


def _proxy_websocket_request_headers(headers, port: int) -> dict[str, str]:
    excluded = {
        "host",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "sec-websocket-accept",
        "sec-websocket-extensions",
        "sec-websocket-key",
        "sec-websocket-protocol",
        "sec-websocket-version",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
    forwarded = {
        key: value
        for key, value in headers.items()
        if key.lower() not in excluded
    }
    forwarded["host"] = f"127.0.0.1:{port}"
    return forwarded


def _proxy_response_headers(headers) -> dict[str, str]:
    excluded = {
        "connection",
        "content-encoding",
        "content-length",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in excluded
    }

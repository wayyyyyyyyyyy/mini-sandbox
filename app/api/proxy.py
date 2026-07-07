import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response

from ..auth import require_api_key


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
        if port < 1 or port > 65535:
            raise HTTPException(status_code=422, detail="proxy port must be between 1 and 65535")

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

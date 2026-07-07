import json
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .schemas import SandboxResponse


def install_response_wrapper(app: FastAPI) -> None:
    @app.middleware("http")
    async def wrap_json_api_response(request: Request, call_next):
        response = await call_next(request)
        if _skip_response_wrapper(request.url.path) or response.headers.get("x-sandbox-wrapped") == "true":
            return response

        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        try:
            data = json.loads(body.decode("utf-8")) if body else None
        except json.JSONDecodeError:
            return JSONResponse(
                status_code=response.status_code,
                content=_response_payload(False, "Invalid JSON response", None),
            )

        success = response.status_code < 400
        message = "Operation successful" if success else _error_message_from_data(data)
        wrapped = _response_payload(success, message, data if success else None)
        return JSONResponse(
            status_code=response.status_code,
            content=wrapped,
            headers=_forward_headers(response.headers),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "HTTP error"
        data = None if isinstance(exc.detail, str) else exc.detail
        return JSONResponse(
            status_code=exc.status_code,
            content=_response_payload(False, message, data),
            headers={"x-sandbox-wrapped": "true"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_response_payload(False, "Validation error", exc.errors()),
            headers={"x-sandbox-wrapped": "true"},
        )


def _response_payload(success: bool, message: str, data: Any, hint: str | None = None) -> dict:
    return SandboxResponse(success=success, message=message, data=data, hint=hint).model_dump()


def _forward_headers(headers) -> dict[str, str]:
    excluded = {"content-length", "content-type"}
    return {key: value for key, value in headers.items() if key.lower() not in excluded}


def _skip_response_wrapper(path: str) -> bool:
    return path in {"/healthz", "/openapi.json"} or path.startswith(("/docs", "/redoc", "/proxy/"))


def _error_message_from_data(data) -> str:
    if isinstance(data, dict) and isinstance(data.get("detail"), str):
        return data["detail"]
    return "HTTP error"

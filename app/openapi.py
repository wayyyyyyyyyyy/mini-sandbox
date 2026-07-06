from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, create_model

from .schemas import SandboxResponse


def install_openapi(app: FastAPI) -> None:
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        _patch_json_response_wrappers(app, schema)
        _patch_file_download(schema)
        _patch_shell_websocket(schema)
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi


def _patch_json_response_wrappers(app: FastAPI, schema: dict[str, Any]) -> None:
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        response_model = getattr(route, "response_model", None)
        if not path or not methods or response_model is None:
            continue
        if _skip_wrapper(path):
            continue
        if path == "/file/download":
            continue

        wrapper_name = _wrapper_component_name(response_model)
        schema["components"]["schemas"][wrapper_name] = _wrapper_schema_for(response_model)
        for method in methods:
            operation = schema["paths"].get(path, {}).get(method.lower())
            if not operation:
                continue
            operation.setdefault("responses", {})
            operation["responses"]["200"] = {
                "description": "Successful Response",
                "content": {
                    "application/json": {
                        "schema": {"$ref": f"#/components/schemas/{wrapper_name}"},
                    }
                },
            }
            _add_error_responses(operation)


def _patch_file_download(schema: dict[str, Any]) -> None:
    operation = schema["paths"].get("/file/download", {}).get("get")
    if not operation:
        return
    operation["responses"]["200"] = {
        "description": "File stream",
        "content": {
            "application/octet-stream": {
                "schema": {
                    "type": "string",
                    "format": "binary",
                }
            }
        },
    }
    _add_error_responses(operation)


def _patch_shell_websocket(schema: dict[str, Any]) -> None:
    schema["x-websockets"] = {
        "/shell/ws": {
            "description": "Persistent interactive shell websocket.",
            "auth": ["X-Sandbox-Api-Key", "Authorization: Bearer", "ticket"],
            "query": {
                "ticket": "One-time ticket from POST /tickets.",
                "session_id": "Optional shell session id.",
                "exec_dir": "Optional workspace-relative working directory.",
                "cols": "Optional terminal columns.",
                "rows": "Optional terminal rows.",
            },
            "client_messages": [
                {"type": "input", "data": "ls -la\n"},
                {"type": "resize", "data": {"cols": 120, "rows": 40}},
                {"type": "pong", "data": {"timestamp": 123}},
            ],
            "server_messages": [
                {"type": "session", "session_id": "sh_xxx"},
                {"type": "output", "data": "..."},
                {"type": "pong", "data": 123},
            ],
        }
    }


def _add_error_responses(operation: dict[str, Any]) -> None:
    for status_code in ("400", "401", "403", "404", "409", "413", "422", "429"):
        operation["responses"][status_code] = {
            "description": "Error Response",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/SandboxResponse"},
                },
            },
        }


def _wrapper_schema_for(response_model: type[BaseModel]) -> dict[str, Any]:
    wrapper_model = create_model(
        _wrapper_component_name(response_model),
        success=(bool, ...),
        message=(str, ...),
        data=(response_model, None),
        hint=(str | None, None),
    )
    return wrapper_model.model_json_schema(ref_template="#/components/schemas/{model}")


def _wrapper_component_name(response_model: type[BaseModel]) -> str:
    return f"Response_{response_model.__name__}_"


def _skip_wrapper(path: str) -> bool:
    return path in {"/healthz", "/openapi.json"} or path.startswith(("/docs", "/redoc"))

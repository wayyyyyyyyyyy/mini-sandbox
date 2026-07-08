from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def required_string(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=422, detail=f"{key} must be a non-empty string")
    return value


def optional_string(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"{key} must be a string")
    return value


def optional_float(arguments: dict[str, Any], key: str) -> float | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, int | float) or value <= 0:
        raise HTTPException(status_code=422, detail=f"{key} must be a positive number")
    return float(value)


def optional_int(arguments: dict[str, Any], key: str) -> int | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or value <= 0:
        raise HTTPException(status_code=422, detail=f"{key} must be a positive integer")
    return value

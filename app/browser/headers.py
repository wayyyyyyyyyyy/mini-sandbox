from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException


def string_headers(headers: dict[str, str]) -> dict[str, str]:
    return {str(name): str(value) for name, value in headers.items()}


def normalize_origin(origin: str) -> str:
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail=f"invalid browser header origin: {origin}")
    return f"{parsed.scheme}://{parsed.netloc}"


def origin_for_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def merge_cdp_headers(original: dict[str, Any], extra: dict[str, str]) -> list[dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for name, value in original.items():
        header_name = str(name)
        merged[header_name.lower()] = {"name": header_name, "value": str(value)}
    for name, value in extra.items():
        header_name = str(name)
        merged[header_name.lower()] = {"name": header_name, "value": str(value)}
    return list(merged.values())

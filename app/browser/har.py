from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlparse


def har_entry(entry: dict[str, Any]) -> dict[str, Any]:
    url = entry.get("url", "")
    request_headers = entry.get("request_headers") or {}
    response_headers = entry.get("response_headers") or {}
    post_data = entry.get("post_data")
    body_size = len(post_data.encode("utf-8")) if isinstance(post_data, str) else -1
    status = entry.get("status")
    encoded_length = entry.get("encoded_data_length")
    return {
        "startedDateTime": _har_started_at(entry.get("wall_time")),
        "time": 0,
        "request": {
            "method": entry.get("method") or "",
            "url": url,
            "httpVersion": "HTTP/1.1",
            "cookies": [],
            "headers": _har_headers(request_headers),
            "queryString": _har_query_string(url),
            "headersSize": -1,
            "bodySize": body_size,
        },
        "response": {
            "status": int(status) if status is not None else 0,
            "statusText": str(entry.get("status_text") or ""),
            "httpVersion": "HTTP/1.1",
            "cookies": [],
            "headers": _har_headers(response_headers),
            "content": {
                "size": int(encoded_length) if encoded_length is not None else 0,
                "mimeType": str(entry.get("mime_type") or ""),
            },
            "redirectURL": "",
            "headersSize": -1,
            "bodySize": int(encoded_length) if encoded_length is not None else -1,
        },
        "cache": {},
        "timings": {"send": 0, "wait": 0, "receive": 0},
    }


def _har_started_at(wall_time: float | None) -> str:
    if wall_time is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return datetime.fromtimestamp(float(wall_time), timezone.utc).isoformat().replace("+00:00", "Z")


def _har_headers(headers: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"name": str(name), "value": str(value)}
        for name, value in headers.items()
    ]


def _har_query_string(url: str) -> list[dict[str, str]]:
    return [
        {"name": name, "value": value}
        for name, value in parse_qsl(urlparse(url).query, keep_blank_values=True)
    ]

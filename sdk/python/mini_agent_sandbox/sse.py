from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any


def parse_sse(body: str) -> list[dict[str, Any]]:
    return list(iter_sse_events(body.splitlines()))


def iter_sse_events(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    event: dict[str, Any] = {}
    data_lines: list[str] = []
    for line in lines:
        if line == "":
            if event or data_lines:
                if data_lines:
                    event["data"] = json.loads("\n".join(data_lines))
                yield event
            event = {}
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            continue
        if value.startswith(" "):
            value = value[1:]
        if field == "id":
            event["id"] = value
        elif field == "event":
            event["event"] = value
        elif field == "data":
            data_lines.append(value)

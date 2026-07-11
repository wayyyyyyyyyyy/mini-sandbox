from __future__ import annotations

import json
from typing import Any


def parse_sse(body: str) -> list[dict[str, Any]]:
    events = []
    for block in body.strip().split("\n\n"):
        event: dict[str, Any] = {}
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("id: "):
                event["id"] = line.removeprefix("id: ")
            elif line.startswith("event: "):
                event["event"] = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data_lines.append(line.removeprefix("data: "))
        if data_lines:
            event["data"] = json.loads("\n".join(data_lines))
        if event:
            events.append(event)
    return events

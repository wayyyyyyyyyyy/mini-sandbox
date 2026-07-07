from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BrowserTab:
    page_id: str
    websocket_url: str
    client: "CdpClient"
    url: str = "about:blank"

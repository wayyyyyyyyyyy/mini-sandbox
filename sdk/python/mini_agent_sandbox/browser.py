from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .client import SandboxClient


class BrowserClient:
    def __init__(self, client: SandboxClient):
        self._client = client

    def navigate(
        self,
        url: str,
        *,
        wait_until: str = "load",
        timeout: int = 30000,
    ) -> dict[str, Any]:
        return self._client._request(
            "POST",
            "/browser/page/navigate",
            json={"url": url, "wait_until": wait_until, "timeout": timeout},
        )

    def text(self) -> str:
        return self._client._request("GET", "/browser/page/text")

    def evaluate(self, script: str) -> dict[str, Any]:
        return self._client._request("POST", "/browser/page/evaluate", json={"script": script})

    def click(self, selector: str, *, timeout: int = 30000) -> dict[str, Any]:
        return self._client._request(
            "POST",
            "/browser/page/click",
            json={"selector": selector, "timeout": timeout},
        )

    def fill(self, selector: str, text: str, *, timeout: int = 30000) -> dict[str, Any]:
        return self._client._request(
            "POST",
            "/browser/page/fill",
            json={"selector": selector, "text": text, "timeout": timeout},
        )

    def wait_for_selector(self, selector: str, *, timeout: int = 30000) -> dict[str, Any]:
        return self._client._request(
            "POST",
            "/browser/page/wait_for_selector",
            json={"selector": selector, "timeout": timeout},
        )

    def screenshot(self, *, format: str = "png", quality: int | None = None) -> bytes:
        params: dict[str, Any] = {"format": format}
        if quality is not None:
            params["quality"] = quality
        return self._client._request_bytes("GET", "/browser/screenshot", params=params)

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .models import BrowserTab
from .runtime import validate_url as _validate_url
from .scripts import (
    click_script as _click_script,
    dispatch_file_input_change_script as _dispatch_file_input_change_script,
    fill_script as _fill_script,
    normalize_expression as _normalize_expression,
    selector_exists_script as _selector_exists_script,
    type_script as _type_script,
)


class BrowserPageMixin:
    def info(self) -> dict[str, Any]:
        with self._lock:
            tab = self._current_tab()
            return {
                "browser": "chromium",
                "headless": True,
                "viewport": {"width": self.width, "height": self.height},
                "page_count": len(self._tabs),
                "current_url": tab.url,
            }

    def navigate(self, *, url: str, wait_until: str, timeout: int) -> dict[str, Any]:
        _validate_url(url)
        with self._lock:
            tab = self._current_tab()
            tab.client.call("Page.navigate", {"url": url}, timeout=timeout / 1000)
            self._wait_for_ready(tab, timeout=timeout / 1000)
            tab.url = self._evaluate(tab, "location.href")
            return {
                "url": tab.url,
                "title": self._evaluate(tab, "document.title"),
                "status": None,
            }

    def html(self, *, outer: bool = False) -> str:
        with self._lock:
            expression = "document.documentElement.outerHTML" if outer else "document.documentElement.innerHTML"
            return self._evaluate(self._current_tab(), expression)

    def text(self) -> str:
        with self._lock:
            return self._evaluate(self._current_tab(), "document.body ? document.body.innerText : ''")

    def evaluate(self, script: str) -> dict[str, Any]:
        with self._lock:
            return {"result": self._evaluate(self._current_tab(), script)}

    def wait_for_selector(self, *, selector: str, timeout: int) -> dict[str, Any]:
        with self._lock:
            tab = self._current_tab()
            self._wait_for_selector(tab, selector=selector, timeout=timeout / 1000)
            return {"selector": selector, "ok": True}

    def click(self, *, selector: str, timeout: int) -> dict[str, Any]:
        with self._lock:
            tab = self._current_tab()
            self._wait_for_selector(tab, selector=selector, timeout=timeout / 1000)
            self._evaluate(tab, _click_script(selector))
            return {"selector": selector, "ok": True}

    def type(self, *, selector: str, text: str, timeout: int) -> dict[str, Any]:
        with self._lock:
            tab = self._current_tab()
            self._wait_for_selector(tab, selector=selector, timeout=timeout / 1000)
            self._evaluate(tab, _type_script(selector, text))
            return {"selector": selector, "ok": True}

    def fill(self, *, selector: str, text: str, timeout: int) -> dict[str, Any]:
        with self._lock:
            tab = self._current_tab()
            self._wait_for_selector(tab, selector=selector, timeout=timeout / 1000)
            self._evaluate(tab, _fill_script(selector, text))
            return {"selector": selector, "ok": True}

    def upload_file(self, *, selector: str, files: list[Path], timeout: int) -> dict[str, Any]:
        with self._lock:
            tab = self._current_tab()
            self._wait_for_selector(tab, selector=selector, timeout=timeout / 1000)
            object_id = self._query_selector_object_id(tab, selector=selector)
            try:
                tab.client.call("DOM.setFileInputFiles", {
                    "objectId": object_id,
                    "files": [str(path) for path in files],
                })
                self._evaluate(tab, _dispatch_file_input_change_script(selector))
            finally:
                tab.client.call("Runtime.releaseObject", {"objectId": object_id})
            return {"selector": selector, "ok": True}

    def screenshot(self, *, image_format: str = "png", quality: int | None = None) -> tuple[bytes, dict[str, str]]:
        if image_format == "jpg":
            image_format = "jpeg"
        if image_format not in {"png", "jpeg"}:
            raise HTTPException(status_code=422, detail="format must be png, jpg, or jpeg")
        params: dict[str, Any] = {"format": image_format}
        if image_format == "jpeg" and quality is not None:
            params["quality"] = quality
        with self._lock:
            data = self._current_tab().client.call("Page.captureScreenshot", params)["data"]
            headers = {
                "x-screen-width": str(self.width),
                "x-screen-height": str(self.height),
                "x-image-width": str(self.width),
                "x-image-height": str(self.height),
            }
            return base64.b64decode(data), headers

    def _wait_for_ready(self, tab: BrowserTab, *, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self._evaluate(tab, "document.readyState")
            if state in {"interactive", "complete"}:
                return
            time.sleep(0.05)
        raise HTTPException(status_code=408, detail="browser navigation timed out")

    def _wait_for_selector(self, tab: BrowserTab, *, selector: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        expression = _selector_exists_script(selector)
        while True:
            if self._evaluate(tab, expression):
                return
            if time.monotonic() >= deadline:
                raise HTTPException(status_code=408, detail=f"browser selector timed out: {selector}")
            time.sleep(0.05)

    def _query_selector_object_id(self, tab: BrowserTab, *, selector: str) -> str:
        document = tab.client.call("DOM.getDocument", {"depth": 0})
        root_id = document.get("root", {}).get("nodeId")
        if root_id is None:
            raise HTTPException(status_code=400, detail="browser DOM document unavailable")
        node = tab.client.call("DOM.querySelector", {
            "nodeId": root_id,
            "selector": selector,
        })
        node_id = node.get("nodeId")
        if not node_id:
            raise HTTPException(status_code=408, detail=f"browser selector timed out: {selector}")
        resolved = tab.client.call("DOM.resolveNode", {"nodeId": node_id})
        object_id = resolved.get("object", {}).get("objectId")
        if object_id is None:
            raise HTTPException(status_code=400, detail=f"browser selector did not resolve to an object: {selector}")
        return object_id

    def _evaluate(self, tab: BrowserTab, expression: str) -> Any:
        result = tab.client.call(
            "Runtime.evaluate",
            {
                "expression": _normalize_expression(expression),
                "awaitPromise": True,
                "returnByValue": True,
            },
        )
        remote = result.get("result", {})
        if "exceptionDetails" in result:
            text = result["exceptionDetails"].get("text", "browser evaluate failed")
            raise HTTPException(status_code=400, detail=text)
        return remote.get("value")

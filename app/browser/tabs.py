from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .cdp import CdpClient
from .models import BrowserTab
from .runtime import validate_url as _validate_url


class BrowserTabsMixin:
    def list_tabs(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_browser()
            return {"tabs": [self._tab_info(index, tab) for index, tab in enumerate(self._tabs)]}

    def create_tab(self, *, url: str | None = None) -> dict[str, Any]:
        if url is not None:
            _validate_url(url)
        with self._lock:
            self._ensure_browser()
            tab = self._new_tab()
            self._tabs.append(tab)
            self._active_index = len(self._tabs) - 1
            if url:
                self.navigate(url=url, wait_until="load", timeout=30000)
            return self._tab_info(self._active_index, tab)

    def activate_tab(self, index: int) -> dict[str, int]:
        with self._lock:
            self._tab_at(index)
            self._active_index = index
            return {"active_index": index}

    def close_tab(self, index: int) -> dict[str, Any]:
        with self._lock:
            self._ensure_browser()
            if len(self._tabs) == 1:
                raise HTTPException(status_code=400, detail="cannot close the last browser tab")
            tab = self._tab_at(index)
            tab.client.close()
            self._json_request(f"http://127.0.0.1:{self._debug_port}/json/close/{tab.page_id}")
            self._tabs.pop(index)
            self._active_index = max(0, min(self._active_index, len(self._tabs) - 1))
            return {"closed": True, "active_index": self._active_index}

    def _current_tab(self) -> BrowserTab:
        self._ensure_browser()
        return self._tabs[self._active_index]

    def _tab_at(self, index: int) -> BrowserTab:
        if index < 0 or index >= len(self._tabs):
            raise HTTPException(status_code=404, detail=f"browser tab not found: {index}")
        return self._tabs[index]

    def _new_tab(self, *, existing: bool = False) -> BrowserTab:
        if existing:
            targets = self._json_request(f"http://127.0.0.1:{self._debug_port}/json")
            target = next(item for item in targets if item.get("type") == "page")
        else:
            target = self._json_request(f"http://127.0.0.1:{self._debug_port}/json/new?about:blank", method="PUT")
        client = CdpClient(target["webSocketDebuggerUrl"])
        client.call("Page.enable")
        client.call("Runtime.enable")
        client.call("DOM.enable")
        client.call("Network.enable")
        self._install_network_handlers(client)
        with self._network_lock:
            headers = dict(self._network_headers)
            needs_fetch = self._needs_fetch_locked()
        if headers:
            client.call("Network.setExtraHTTPHeaders", {"headers": headers})
        if needs_fetch:
            client.call("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})
        client.call("Page.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": str(self._download_dir()),
        })
        client.call("Emulation.setDeviceMetricsOverride", {
            "width": self.width,
            "height": self.height,
            "deviceScaleFactor": 1,
            "mobile": False,
        })
        return BrowserTab(page_id=target["id"], websocket_url=target["webSocketDebuggerUrl"], client=client)

    def _tab_info(self, index: int, tab: BrowserTab) -> dict[str, Any]:
        tab.url = self._evaluate(tab, "location.href")
        return {
            "index": index,
            "url": tab.url,
            "title": self._evaluate(tab, "document.title"),
            "active": index == self._active_index,
        }

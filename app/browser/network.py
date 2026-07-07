from __future__ import annotations

import base64
import fnmatch
from typing import Any

from .cdp import CdpClient
from .har import har_entry
from .headers import merge_cdp_headers, normalize_origin, origin_for_url, string_headers


class BrowserNetworkMixin:
    def network_requests(self, *, filter_text: str | None = None, limit: int = 100) -> dict[str, Any]:
        with self._network_lock:
            requests = list(self._network_requests)
        if filter_text:
            requests = [request for request in requests if filter_text in request["url"]]
        if limit >= 0:
            requests = requests[-limit:]
        return {"requests": requests}

    def set_network_headers(self, *, headers: dict[str, str]) -> dict[str, Any]:
        normalized = string_headers(headers)
        with self._lock:
            self._ensure_browser()
            with self._network_lock:
                self._network_headers = normalized
            for tab in self._tabs:
                tab.client.call("Network.setExtraHTTPHeaders", {"headers": normalized})
            return {"headers": normalized}

    def set_network_scoped_headers(self, *, origin: str, headers: dict[str, str]) -> dict[str, Any]:
        normalized_origin = normalize_origin(origin)
        normalized_headers = string_headers(headers)
        with self._lock:
            self._ensure_browser()
            with self._network_lock:
                self._network_scoped_headers[normalized_origin] = normalized_headers
            self._sync_fetch_state()
            return {"origin": normalized_origin, "headers": normalized_headers}

    def export_har(self) -> dict[str, Any]:
        with self._network_lock:
            requests = [dict(request) for request in self._network_requests]
        entries = [har_entry(request) for request in requests]
        return {
            "entries": len(entries),
            "har": {
                "log": {
                    "version": "1.2",
                    "creator": {"name": "mini-agent-sandbox", "version": "0.1.0"},
                    "entries": entries,
                }
            },
        }

    def add_network_route(
        self,
        *,
        url_pattern: str,
        response: dict[str, Any] | None,
        abort: bool,
    ) -> dict[str, Any]:
        with self._lock:
            self._ensure_browser()
            with self._network_lock:
                self._network_routes[url_pattern] = {
                    "url_pattern": url_pattern,
                    "response": response,
                    "abort": abort,
                }
            self._sync_fetch_state()
            return {"url_pattern": url_pattern, "active": True, "abort": abort}

    def remove_network_route(self, *, url_pattern: str) -> dict[str, Any]:
        with self._lock:
            with self._network_lock:
                removed = self._network_routes.pop(url_pattern, None) is not None
            self._sync_fetch_state()
            return {"url_pattern": url_pattern, "removed": removed}

    def _install_network_handlers(self, client: CdpClient) -> None:
        client.add_event_handler("Network.requestWillBeSent", self._on_network_request)
        client.add_event_handler("Network.responseReceived", self._on_network_response)
        client.add_event_handler("Network.loadingFailed", self._on_network_failed)
        client.add_event_handler("Network.loadingFinished", self._on_network_finished)
        client.add_event_handler("Fetch.requestPaused", lambda params: self._on_fetch_paused(client, params))

    def _on_network_request(self, params: dict[str, Any]) -> None:
        request = params.get("request", {})
        request_id = params.get("requestId")
        if not request_id:
            return
        entry = {
            "request_id": request_id,
            "url": request.get("url", ""),
            "method": request.get("method", ""),
            "resource_type": params.get("type", ""),
            "timestamp": params.get("timestamp"),
            "wall_time": params.get("wallTime"),
            "request_headers": dict(request.get("headers") or {}),
            "post_data": request.get("postData"),
            "status": None,
            "failed": False,
            "error_text": None,
        }
        with self._network_lock:
            self._network_requests.append(entry)
            if len(self._network_requests) > 1000:
                self._network_requests = self._network_requests[-1000:]

    def _on_network_response(self, params: dict[str, Any]) -> None:
        request_id = params.get("requestId")
        response = params.get("response", {})
        with self._network_lock:
            entry = self._network_entry(request_id)
            if entry is not None:
                entry["status"] = response.get("status")
                entry["status_text"] = response.get("statusText")
                entry["mime_type"] = response.get("mimeType")
                entry["response_headers"] = dict(response.get("headers") or {})

    def _on_network_failed(self, params: dict[str, Any]) -> None:
        request_id = params.get("requestId")
        with self._network_lock:
            entry = self._network_entry(request_id)
            if entry is not None:
                entry["failed"] = True
                entry["error_text"] = params.get("errorText")

    def _on_network_finished(self, params: dict[str, Any]) -> None:
        request_id = params.get("requestId")
        with self._network_lock:
            entry = self._network_entry(request_id)
            if entry is not None:
                entry["finished"] = True
                entry["encoded_data_length"] = params.get("encodedDataLength")

    def _on_fetch_paused(self, client: CdpClient, params: dict[str, Any]) -> None:
        request_id = params.get("requestId")
        request = params.get("request", {})
        url = request.get("url", "")
        if not request_id:
            return
        with self._network_lock:
            route = next(
                (
                    item
                    for pattern, item in self._network_routes.items()
                    if fnmatch.fnmatch(url, pattern)
                ),
                None,
            )
            scoped_headers = self._scoped_headers_for_url_locked(url)
        if route is None:
            params = {"requestId": request_id}
            if scoped_headers:
                params["headers"] = merge_cdp_headers(request.get("headers") or {}, scoped_headers)
            client.send("Fetch.continueRequest", params)
            return
        if route.get("abort"):
            client.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "Aborted"})
            return
        response = route.get("response") or {}
        body = str(response.get("body", ""))
        headers = dict(response.get("headers") or {})
        content_type = response.get("content_type") or "text/plain"
        headers.setdefault("content-type", str(content_type))
        client.send("Fetch.fulfillRequest", {
            "requestId": request_id,
            "responseCode": int(response.get("status", 200)),
            "responseHeaders": [
                {"name": str(name), "value": str(value)}
                for name, value in headers.items()
            ],
            "body": base64.b64encode(body.encode("utf-8")).decode("ascii"),
        })

    def _sync_fetch_state(self) -> None:
        with self._network_lock:
            needs_fetch = self._needs_fetch_locked()
        for tab in self._tabs:
            if needs_fetch:
                tab.client.call("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})
            else:
                tab.client.call("Fetch.disable")

    def _needs_fetch_locked(self) -> bool:
        return bool(self._network_routes or self._network_scoped_headers)

    def _scoped_headers_for_url_locked(self, url: str) -> dict[str, str]:
        origin = origin_for_url(url)
        if origin is None:
            return {}
        return dict(self._network_scoped_headers.get(origin) or {})

    def _network_entry(self, request_id: str | None) -> dict[str, Any] | None:
        if request_id is None:
            return None
        for entry in reversed(self._network_requests):
            if entry["request_id"] == request_id:
                return entry
        return None


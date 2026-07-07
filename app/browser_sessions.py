from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import HTTPException
from websockets.sync.client import connect


@dataclass
class BrowserTab:
    page_id: str
    websocket_url: str
    client: "CdpClient"
    url: str = "about:blank"


class BrowserSessionManager:
    def __init__(
        self,
        *,
        width: int = 1280,
        height: int = 720,
        download_dir: Callable[[], Path] | None = None,
    ) -> None:
        self.width = width
        self.height = height
        self._download_dir = download_dir or _default_download_dir
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._user_data_dir: tempfile.TemporaryDirectory | None = None
        self._debug_port: int | None = None
        self._tabs: list[BrowserTab] = []
        self._active_index = 0

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

    def save_state(self) -> dict[str, Any]:
        with self._lock:
            tab = self._current_tab()
            cookies = tab.client.call("Network.getAllCookies").get("cookies", [])
            origin = self._evaluate(tab, _local_storage_state_script())
            origins = [origin] if origin is not None else []
            return {
                "cookies": cookies,
                "origins": origins,
            }

    def load_state(self, state: dict[str, Any]) -> dict[str, int]:
        with self._lock:
            tab = self._current_tab()
            cookies = state.get("cookies", [])
            if not isinstance(cookies, list):
                raise HTTPException(status_code=400, detail="browser state cookies must be a list")
            cookie_params = [_cookie_param(cookie) for cookie in cookies if isinstance(cookie, dict)]
            if cookie_params:
                tab.client.call("Network.setCookies", {"cookies": cookie_params})

            current_origin = self._evaluate(tab, "location.origin")
            origins = state.get("origins", [])
            if not isinstance(origins, list):
                raise HTTPException(status_code=400, detail="browser state origins must be a list")
            restored_origins = 0
            for origin_state in origins:
                if not isinstance(origin_state, dict) or origin_state.get("origin") != current_origin:
                    continue
                local_storage = origin_state.get("localStorage", {})
                if not isinstance(local_storage, dict):
                    raise HTTPException(status_code=400, detail="browser state localStorage must be an object")
                self._evaluate(tab, _restore_local_storage_script(local_storage))
                restored_origins += 1
            return {
                "cookies": len(cookie_params),
                "origins": restored_origins,
            }

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

    def close(self) -> None:
        with self._lock:
            for tab in self._tabs:
                tab.client.close()
            self._tabs = []
            if self._process is not None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=5)
                self._process = None
            self._cleanup_user_data_dir()
            self._debug_port = None
            self._active_index = 0

    def _current_tab(self) -> BrowserTab:
        self._ensure_browser()
        return self._tabs[self._active_index]

    def _tab_at(self, index: int) -> BrowserTab:
        if index < 0 or index >= len(self._tabs):
            raise HTTPException(status_code=404, detail=f"browser tab not found: {index}")
        return self._tabs[index]

    def _ensure_browser(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        executable = _chromium_executable()
        self._debug_port = _free_port()
        self._user_data_dir = tempfile.TemporaryDirectory(prefix="mini-browser-")
        command = [
            str(executable),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            f"--remote-debugging-port={self._debug_port}",
            f"--user-data-dir={self._user_data_dir.name}",
            f"--window-size={self.width},{self.height}",
            "about:blank",
        ]
        self._process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._wait_for_debugger()
        self._configure_downloads()
        self._tabs = [self._new_tab(existing=True)]
        self._active_index = 0

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

    def _wait_for_debugger(self) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                self._json_request(f"http://127.0.0.1:{self._debug_port}/json/version")
                return
            except Exception:
                time.sleep(0.05)
        raise HTTPException(status_code=503, detail="browser debugger did not start")

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

    def _tab_info(self, index: int, tab: BrowserTab) -> dict[str, Any]:
        tab.url = self._evaluate(tab, "location.href")
        return {
            "index": index,
            "url": tab.url,
            "title": self._evaluate(tab, "document.title"),
            "active": index == self._active_index,
        }

    def _json_request(self, url: str, *, method: str = "GET") -> Any:
        request = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8")
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return body

    def _configure_downloads(self) -> None:
        if self._debug_port is None:
            return
        download_dir = self._download_dir()
        download_dir.mkdir(parents=True, exist_ok=True)
        version = self._json_request(f"http://127.0.0.1:{self._debug_port}/json/version")
        client = CdpClient(version["webSocketDebuggerUrl"])
        try:
            client.call("Browser.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": str(download_dir),
                "eventsEnabled": True,
            })
        finally:
            client.close()

    def _cleanup_user_data_dir(self) -> None:
        directory = self._user_data_dir
        self._user_data_dir = None
        if directory is None:
            return
        for attempt in range(5):
            try:
                directory.cleanup()
                return
            except PermissionError:
                if attempt == 4:
                    return
                time.sleep(0.1 * (attempt + 1))


class CdpClient:
    def __init__(self, websocket_url: str) -> None:
        self.websocket_url = websocket_url
        self._socket = connect(websocket_url, open_timeout=5)
        self._next_id = 1
        self._lock = threading.Lock()

    def call(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 5) -> dict[str, Any]:
        with self._lock:
            message_id = self._next_id
            self._next_id += 1
            self._socket.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                raw = self._socket.recv(timeout=max(deadline - time.monotonic(), 0))
                message = json.loads(raw)
                if message.get("id") != message_id:
                    continue
                if "error" in message:
                    raise HTTPException(status_code=400, detail=message["error"].get("message", "CDP error"))
                return message.get("result", {})
            raise HTTPException(status_code=408, detail=f"CDP method timed out: {method}")

    def close(self) -> None:
        try:
            self._socket.close()
        except Exception:
            pass


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "about", "data"}:
        raise HTTPException(status_code=400, detail=f"unsupported browser URL scheme: {parsed.scheme}")


def _chromium_executable() -> Path:
    root = Path(__file__).resolve().parents[1]
    env_path = os.getenv("CHROMIUM_EXECUTABLE")
    if env_path and Path(env_path).exists():
        return Path(env_path)

    browser_roots = [root / ".ms-playwright"]
    playwright_browser_path = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if playwright_browser_path:
        browser_roots.insert(0, Path(playwright_browser_path))

    if sys.platform == "win32":
        candidates = [
            browser_root / "chromium_headless_shell-1148" / "chrome-win" / "headless_shell.exe"
            for browser_root in browser_roots
        ] + [
            browser_root / "chromium-1148" / "chrome-win" / "chrome.exe"
            for browser_root in browser_roots
        ]
    else:
        candidates = [
            browser_root / "chromium_headless_shell-1148" / "chrome-linux" / "headless_shell"
            for browser_root in browser_roots
        ] + [
            browser_root / "chromium-1148" / "chrome-linux" / "chrome"
            for browser_root in browser_roots
        ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise HTTPException(status_code=503, detail="Chromium executable not found; run playwright install chromium")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _default_download_dir() -> Path:
    from .config import WORKSPACE

    return WORKSPACE / "Downloads"


def _normalize_expression(expression: str) -> str:
    stripped = expression.strip()
    if stripped.startswith("() =>") or stripped.startswith("async () =>"):
        return f"({stripped})()"
    return expression


def _selector_exists_script(selector: str) -> str:
    selector_json = json.dumps(selector)
    return f"document.querySelector({selector_json}) !== null"


def _click_script(selector: str) -> str:
    selector_json = json.dumps(selector)
    return f"""
(() => {{
  const el = document.querySelector({selector_json});
  if (!el) return false;
  el.scrollIntoView({{block: 'center', inline: 'center'}});
  if (typeof el.focus === 'function') el.focus();
  el.click();
  return true;
}})()
"""


def _type_script(selector: str, text: str) -> str:
    selector_json = json.dumps(selector)
    text_json = json.dumps(text)
    return f"""
(() => {{
  const el = document.querySelector({selector_json});
  const text = {text_json};
  if (!el) return false;
  el.scrollIntoView({{block: 'center', inline: 'center'}});
  if (typeof el.focus === 'function') el.focus();
  if ('value' in el) {{
    const current = String(el.value ?? '');
    const start = Number.isInteger(el.selectionStart) ? el.selectionStart : current.length;
    const end = Number.isInteger(el.selectionEnd) ? el.selectionEnd : start;
    el.value = current.slice(0, start) + text + current.slice(end);
    const cursor = start + text.length;
    if (typeof el.setSelectionRange === 'function') el.setSelectionRange(cursor, cursor);
  }} else {{
    el.textContent = String(el.textContent ?? '') + text;
  }}
  el.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText', data: text}}));
  return true;
}})()
"""


def _fill_script(selector: str, text: str) -> str:
    selector_json = json.dumps(selector)
    text_json = json.dumps(text)
    return f"""
(() => {{
  const el = document.querySelector({selector_json});
  const text = {text_json};
  if (!el) return false;
  el.scrollIntoView({{block: 'center', inline: 'center'}});
  if (typeof el.focus === 'function') el.focus();
  if ('value' in el) {{
    el.value = text;
    if (typeof el.setSelectionRange === 'function') {{
      const cursor = text.length;
      el.setSelectionRange(cursor, cursor);
    }}
  }} else {{
    el.textContent = text;
  }}
  el.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertReplacementText', data: text}}));
  el.dispatchEvent(new Event('change', {{bubbles: true}}));
  return true;
}})()
"""


def _dispatch_file_input_change_script(selector: str) -> str:
    selector_json = json.dumps(selector)
    return f"""
(() => {{
  const el = document.querySelector({selector_json});
  if (!el) return false;
  el.dispatchEvent(new Event('input', {{bubbles: true}}));
  el.dispatchEvent(new Event('change', {{bubbles: true}}));
  return true;
}})()
"""


def _local_storage_state_script() -> str:
    return """
(() => {
  try {
    if (!location.origin || location.origin === 'null') return null;
    const localStorageItems = {};
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      localStorageItems[key] = localStorage.getItem(key);
    }
    return {origin: location.origin, localStorage: localStorageItems};
  } catch {
    return null;
  }
})()
"""


def _restore_local_storage_script(local_storage: dict[str, Any]) -> str:
    local_storage_json = json.dumps({
        str(key): "" if value is None else str(value)
        for key, value in local_storage.items()
    })
    return f"""
(() => {{
  const items = {local_storage_json};
  localStorage.clear();
  for (const [key, value] of Object.entries(items)) {{
    localStorage.setItem(key, value);
  }}
  return true;
}})()
"""


def _cookie_param(cookie: dict[str, Any]) -> dict[str, Any]:
    required = {"name", "value"}
    if not required.issubset(cookie):
        raise HTTPException(status_code=400, detail="browser cookie missing name or value")

    param: dict[str, Any] = {
        "name": str(cookie["name"]),
        "value": str(cookie["value"]),
    }
    for key in (
        "domain",
        "path",
        "secure",
        "httpOnly",
        "sameSite",
        "priority",
        "sameParty",
        "sourceScheme",
        "sourcePort",
    ):
        if key in cookie and cookie[key] is not None:
            param[key] = cookie[key]
    if not cookie.get("session") and cookie.get("expires") is not None:
        param["expires"] = cookie["expires"]
    return param

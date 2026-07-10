from __future__ import annotations

import json
import subprocess
import tempfile
import time
import urllib.request
from typing import Any

from fastapi import HTTPException

from .cdp import CdpClient
from .runtime import (
    chromium_executable as _chromium_executable,
    free_port as _free_port,
)


class BrowserProcessMixin:
    def restart(self, *, mode: str = "hard", clear_routes: bool = True) -> dict[str, Any]:
        with self._lock:
            with self._network_lock:
                if clear_routes:
                    self._network_routes = {}
                self._network_requests = []
            self.close()
            self._ensure_browser()
            tab = self._current_tab()
            return {
                "mode": mode,
                "restarted": True,
                "page_count": len(self._tabs),
                "current_url": tab.url,
                "routes_cleared": clear_routes,
            }

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
        with self._network_lock:
            self._network_requests = []

    def _wait_for_debugger(self) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                self._json_request(f"http://127.0.0.1:{self._debug_port}/json/version")
                return
            except Exception:
                time.sleep(0.05)
        raise HTTPException(status_code=503, detail="browser debugger did not start")

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

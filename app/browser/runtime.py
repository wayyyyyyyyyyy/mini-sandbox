from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "about", "data"}:
        raise HTTPException(status_code=400, detail=f"unsupported browser URL scheme: {parsed.scheme}")


def chromium_executable() -> Path:
    root = Path(__file__).resolve().parents[2]
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


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def default_download_dir() -> Path:
    from ..config import WORKSPACE

    return WORKSPACE / "Downloads"

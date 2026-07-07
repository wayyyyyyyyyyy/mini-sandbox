from __future__ import annotations

import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable

from .models import BrowserTab
from .network import BrowserNetworkMixin
from .page import BrowserPageMixin
from .process import BrowserProcessMixin
from .runtime import default_download_dir as _default_download_dir
from .state import BrowserStateMixin
from .tabs import BrowserTabsMixin


class BrowserSessionManager(
    BrowserProcessMixin,
    BrowserPageMixin,
    BrowserStateMixin,
    BrowserNetworkMixin,
    BrowserTabsMixin,
):
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
        self._network_lock = threading.Lock()
        self._network_requests: list[dict] = []
        self._network_routes: dict[str, dict] = {}
        self._network_headers: dict[str, str] = {}
        self._network_scoped_headers: dict[str, dict[str, str]] = {}

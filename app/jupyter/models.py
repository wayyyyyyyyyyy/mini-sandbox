from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from jupyter_client import KernelManager


@dataclass
class JupyterSession:
    session_id: str
    kernel_name: str
    cwd: Path
    manager: KernelManager
    client: Any
    created_at: datetime
    last_used_at: datetime
    lock: Lock = field(default_factory=Lock)


def utcnow() -> datetime:
    return datetime.now(UTC)

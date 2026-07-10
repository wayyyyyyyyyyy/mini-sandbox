from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class ShellSession:
    session_id: str
    working_dir: Path
    workspace: Path
    created_at: datetime
    last_used_at: datetime
    env: dict[str, str] = field(default_factory=dict)
    status: str = "ready"
    current_command: str | None = None
    current_process: subprocess.Popen[str] | None = None
    current_master_fd: int | None = None
    no_change_timeout: int | None = None
    output: str = ""
    exit_code: int | None = None
    killed: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    output_changed: threading.Condition = field(init=False)

    def __post_init__(self) -> None:
        self.output_changed = threading.Condition(self.lock)

    @property
    def command_status(self) -> str:
        if self.status == "closed":
            return "closed"
        if self.killed:
            return "killed"
        if self.current_process is not None and self.current_process.poll() is None:
            return "running"
        if self.current_command is not None:
            return "completed"
        return self.status


def utcnow() -> datetime:
    return datetime.now(UTC)

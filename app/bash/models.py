from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class BashCommand:
    command_id: str
    command: str
    process: subprocess.Popen[str]
    started_at: float
    hard_timeout: float | None
    stdout: str = ""
    stderr: str = ""
    stdout_closed: bool = False
    stderr_closed: bool = False
    killed: bool = False
    timed_out: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    output_changed: threading.Condition = field(init=False)

    def __post_init__(self) -> None:
        self.output_changed = threading.Condition(self.lock)

    @property
    def status(self) -> str:
        if self.killed:
            return "killed"
        if self.timed_out:
            return "timed_out"
        if self.process.poll() is None:
            return "running"
        return "completed"

    @property
    def exit_code(self) -> int | None:
        return self.process.poll()


@dataclass
class BashSession:
    session_id: str
    working_dir: Path
    created_at: datetime
    last_used_at: datetime
    status: str = "ready"
    snapshot_path: str | None = None
    current_command_id: str | None = None
    commands: dict[str, BashCommand] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def command_count(self) -> int:
        return len(self.commands)

    @property
    def current_command(self) -> BashCommand | None:
        if not self.current_command_id:
            return None
        return self.commands.get(self.current_command_id)


def utcnow() -> datetime:
    return datetime.now(UTC)

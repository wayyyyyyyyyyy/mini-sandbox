from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from fastapi import HTTPException

from .models import BashCommand


def start_process(command: str, *, cwd: Path, env: dict[str, str]) -> subprocess.Popen[str]:
    merged_env = os.environ.copy()
    merged_env.update(env)
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=merged_env,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        bufsize=1,
    )


def terminate(command: BashCommand, signal_name: str) -> None:
    if command.process.poll() is not None:
        return
    normalized = signal_name.upper()
    if normalized not in {"SIGTERM", "SIGKILL", "SIGINT"}:
        raise HTTPException(status_code=400, detail=f"unsupported signal: {signal_name}")
    with command.output_changed:
        command.killed = True
        command.output_changed.notify_all()
    if os.name == "nt":
        command.process.kill()
        return
    command.process.send_signal(getattr(signal, normalized))


def kill_process(command: BashCommand) -> None:
    command.process.kill()
